"""Bootstrap configuration for architecture v2.

Only startup-level settings live here. User-managed resources such as providers,
actors, ingress rules, and service credentials are persisted in DB tables and
loaded through the Resources root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Self

import msgspec
import yaml
from dotenv import load_dotenv

from yuubot.core.secrets import master_key_for_tests, master_key_is_valid

_ENV_RE = re.compile(r"\$\{(\w+)\}")


class BootstrapConfigError(ValueError):
    """Raised when bootstrap config cannot safely start the process."""


class HostPort(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    host: str
    port: int


class AdminConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    host: str
    port: int
    secret: str
    web_dist_dir: str = ""  # empty = auto-detect from package root


class ServerConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    daemon_host: str
    daemon_port: int
    daemon_secret: str


class DatabaseConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Concrete platform DB path for file-loaded bootstrap config."""

    path: str


class SecretConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    master_key: str


class TraceConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    enabled: bool
    collector_host: str
    collector_port: int


class PathsConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Single root for every yuubot on-disk artifact.

    See ``yuubot.bootstrap.layout.DataLayout`` for derived subpaths
    (``<data_dir>/yuubot/yuubot.db``, ``<data_dir>/integrations/...``,
    ``<data_dir>/workspace/actors/...``, etc.). Docker deployments mount
    only ``data_dir``.
    """

    data_dir: str


class BootstrapConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    admin: AdminConfig
    server: ServerConfig
    database: DatabaseConfig
    secrets: SecretConfig
    trace: TraceConfig
    paths: PathsConfig

    def validate(self) -> Self:
        _require_non_empty("admin.host", self.admin.host)
        _require_non_empty("server.daemon_host", self.server.daemon_host)
        _require_non_empty("database.path", self.database.path)
        _require_non_empty("paths.data_dir", self.paths.data_dir)
        if not _is_loopback_host(self.admin.host) and not self.admin.secret:
            msg = "admin.secret is required when admin.host is not loopback"
            raise BootstrapConfigError(msg)
        if (
            not _is_loopback_host(self.server.daemon_host)
            and not self.server.daemon_secret
        ):
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
            admin=AdminConfig(
                host="127.0.0.1",
                port=8781,
                secret="",
                web_dist_dir=".",
            ),
            server=ServerConfig(
                daemon_host="127.0.0.1",
                daemon_port=8780,
                daemon_secret=daemon_secret,
            ),
            database=DatabaseConfig(path=database_path),
            secrets=SecretConfig(master_key=master_key),
            trace=TraceConfig(
                enabled=False,
                collector_host="127.0.0.1",
                collector_port=4318,
            ),
            paths=PathsConfig(data_dir=data_dir),
        )


def resolve_web_dist_dir(web_dist_dir: str) -> Path:
    """Resolve the web dist directory.

    When web_dist_dir is empty (default), resolves relative to the
    yuubot package root (apps/yuubot/web/dist). Otherwise resolves
    the explicit path relative to CWD.
    """
    if web_dist_dir:
        return Path(web_dist_dir).expanduser().resolve()
    # config.py lives at apps/yuubot/src/yuubot/bootstrap/config.py
    # package root (apps/yuubot/) is 4 parents up
    pkg_root = Path(__file__).parent.parent.parent.parent
    return (pkg_root / "web" / "dist").resolve()


def load_bootstrap_config(config_path: str | Path | None = None) -> BootstrapConfig:
    """Load `.env` and a v2 `config.yaml` into typed bootstrap settings."""

    load_dotenv()
    if config_path is None:
        msg = "--config is required for bootstrap config loading"
        raise BootstrapConfigError(msg)
    path = Path(config_path).expanduser()
    if not path.exists():
        raise BootstrapConfigError(f"config file does not exist: {path}")
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        raise BootstrapConfigError("config.yaml root must be a mapping")
    resolved = _walk_expand_paths(_walk_resolve_env(loaded))
    try:
        config = msgspec.convert(resolved, type=BootstrapConfig, strict=True)
    except msgspec.ValidationError as exc:
        raise BootstrapConfigError(f"invalid bootstrap config: {exc}") from exc
    return config.validate()


def _resolve_env(value: str) -> str:
    return _ENV_RE.sub(lambda match: os.environ.get(match.group(1), ""), value)


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise BootstrapConfigError(f"{name} must be set")


def _walk_resolve_env(value: object) -> object:
    if isinstance(value, str):
        return _resolve_env(value)
    if isinstance(value, list):
        return [_walk_resolve_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _walk_resolve_env(item) for key, item in value.items()}
    return value


def _walk_expand_paths(value: object) -> object:
    path_keys = {"path", "data_dir"}
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_walk_expand_paths(item) for item in value]
    if isinstance(value, dict):
        expanded: dict[object, object] = {}
        for key, item in value.items():
            if (
                key in path_keys
                and isinstance(item, str)
                and item not in {":memory:", ""}
            ):
                expanded[key] = str(Path(item).expanduser())
            else:
                expanded[key] = _walk_expand_paths(item)
        return expanded
    return value
