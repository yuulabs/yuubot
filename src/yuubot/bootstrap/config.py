"""Bootstrap configuration for architecture v2.

Only startup-level settings live here. User-managed resources such as providers,
characters, actors, ingress rules, and service credentials are persisted in
DB tables and loaded through the Resources root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Self

import msgspec
import yaml
from dotenv import load_dotenv

from yuubot.core.secrets import master_key_for_tests, master_key_is_valid

_ENV_RE = re.compile(r"\$\{(\w+)\}")


class BootstrapConfigError(ValueError):
    """Raised when bootstrap config cannot safely start the process."""


class HostPort(msgspec.Struct, frozen=True):
    host: str = "127.0.0.1"
    port: int = 0


class AdminConfig(msgspec.Struct, frozen=True):
    host: str = "127.0.0.1"
    port: int = 8781
    secret: str = ""
    web_dist_dir: str = "web/dist"


class ServerConfig(msgspec.Struct, frozen=True):
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 8780
    daemon_secret: str = ""


class DatabaseConfig(msgspec.Struct, frozen=True):
    """Optional override for the platform DB.

    When ``path`` is empty, the daemon resolves it as
    ``DataLayout(paths.data_dir).db_path``. Tests may override with
    ``":memory:"`` or a temp file.
    """

    path: str = ""


class SecretConfig(msgspec.Struct, frozen=True):
    master_key: str = ""


class TraceConfig(msgspec.Struct, frozen=True):
    enabled: bool = True
    collector_host: str = "127.0.0.1"
    collector_port: int = 4318
    ui_host: str = "127.0.0.1"
    ui_port: int = 8782


class PathsConfig(msgspec.Struct, frozen=True):
    """Single root for every yuubot on-disk artifact.

    See ``yuubot.bootstrap.layout.DataLayout`` for derived subpaths
    (``<data_dir>/yuubot/yuubot.db``, ``<data_dir>/integrations/...``,
    ``<data_dir>/workspace/actors/...``, etc.). Docker deployments mount
    only ``data_dir``.
    """

    data_dir: str = "~/.yuubot"


class YuuAgentsConfig(msgspec.Struct, frozen=True):
    """Static yuuagents infrastructure config; daemon restart required."""

    strict: bool = False
    tool_backends: dict[str, dict[str, object]] = msgspec.field(default_factory=dict)


class BootstrapConfig(msgspec.Struct, frozen=True):
    admin: AdminConfig = msgspec.field(default_factory=AdminConfig)
    server: ServerConfig = msgspec.field(default_factory=ServerConfig)
    database: DatabaseConfig = msgspec.field(default_factory=DatabaseConfig)
    secrets: SecretConfig = msgspec.field(default_factory=SecretConfig)
    trace: TraceConfig = msgspec.field(default_factory=TraceConfig)
    paths: PathsConfig = msgspec.field(default_factory=PathsConfig)
    yuuagents: YuuAgentsConfig = msgspec.field(default_factory=YuuAgentsConfig)

    def validate(self) -> Self:
        if not _is_loopback_host(self.admin.host) and not self.admin.secret:
            msg = "admin.secret is required when admin.host is not loopback"
            raise BootstrapConfigError(msg)
        if not _is_loopback_host(self.server.daemon_host) and not self.server.daemon_secret:
            msg = "server.daemon_secret is required when daemon_host is not loopback"
            raise BootstrapConfigError(msg)
        if not self.secrets.master_key:
            raise BootstrapConfigError("secrets.master_key must be set")
        if not master_key_is_valid(self.secrets.master_key):
            raise BootstrapConfigError("secrets.master_key must be 32 bytes base64")
        return self

    @classmethod
    def for_tests(
        cls,
        *,
        database_path: str = ":memory:",
        master_key: str = master_key_for_tests(),
        daemon_secret: str = "test-daemon-secret",
        data_dir: str = "~/.yuubot-test",
    ) -> Self:
        return cls(
            server=ServerConfig(daemon_secret=daemon_secret),
            database=DatabaseConfig(path=database_path),
            secrets=SecretConfig(master_key=master_key),
            trace=TraceConfig(enabled=False),
            paths=PathsConfig(data_dir=data_dir),
        )


def load_bootstrap_config(config_path: str | Path | None = None) -> BootstrapConfig:
    """Load `.env` and a v2 `config.yaml` into typed bootstrap settings."""

    load_dotenv()
    raw: dict[str, Any] = {}
    if config_path:
        path = Path(config_path).expanduser()
        if path.exists():
            loaded = yaml.safe_load(path.read_text()) or {}
            if not isinstance(loaded, dict):
                raise BootstrapConfigError("config.yaml root must be a mapping")
            raw = loaded
    resolved = _walk_expand_paths(_walk_resolve_env(raw))
    return msgspec.convert(resolved, type=BootstrapConfig, strict=False).validate()


def _resolve_env(value: str) -> str:
    return _ENV_RE.sub(lambda match: os.environ.get(match.group(1), ""), value)


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _walk_resolve_env(value: Any) -> Any:
    if isinstance(value, str):
        return _resolve_env(value)
    if isinstance(value, list):
        return [_walk_resolve_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _walk_resolve_env(item) for key, item in value.items()}
    return value


def _walk_expand_paths(value: Any) -> Any:
    path_keys = {"path", "data_dir"}
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_walk_expand_paths(item) for item in value]
    if isinstance(value, dict):
        expanded: dict[str, Any] = {}
        for key, item in value.items():
            if key in path_keys and isinstance(item, str) and item not in {":memory:", ""}:
                expanded[key] = str(Path(item).expanduser())
            else:
                expanded[key] = _walk_expand_paths(item)
        return expanded
    return value
