"""Process startup and deployment configuration."""

import os
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

import msgspec
import yaml

from ..python import PythonKernelsConfig, python_kernels_config_from_raw
from ..runtime.resource_config import ResourceConfig, resource_config_from_raw

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class ServerConfig(msgspec.Struct, frozen=True):
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


class AdminAuthBuiltinConfig(msgspec.Struct, frozen=True):
    session_cookie_name: str = "yuubot_session"
    csrf_header: str = "X-CSRF-Token"
    password: str = ""


class AdminAuthProxyConfig(msgspec.Struct, frozen=True):
    user_header: str = "X-Forwarded-User"
    groups_header: str | None = "X-Forwarded-Groups"


class AdminAuthConfig(msgspec.Struct, frozen=True):
    mode: Literal["proxy", "builtin", "loopback_bypass"] = "loopback_bypass"
    builtin: AdminAuthBuiltinConfig = msgspec.field(default_factory=AdminAuthBuiltinConfig)
    proxy: AdminAuthProxyConfig = msgspec.field(default_factory=AdminAuthProxyConfig)


class DeploymentConfig(msgspec.Struct, frozen=True):
    server: ServerConfig = msgspec.field(default_factory=ServerConfig)
    admin_url_base: str = ""
    public_url_base: str = ""
    trusted_proxies: tuple[str, ...] = ()
    admin_auth: AdminAuthConfig = msgspec.field(default_factory=AdminAuthConfig)


class ProcessConfig(msgspec.Struct, frozen=True, kw_only=True):
    data_dir: str = ".yuubot-data"
    python_kernels: PythonKernelsConfig = msgspec.field(default_factory=PythonKernelsConfig)
    resources: ResourceConfig = msgspec.field(default_factory=ResourceConfig)


def load_yaml_mapping(path: str | Path) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError("config must be a mapping")
    return cast(dict[str, object], data)


def process_config_from_raw(raw: dict[str, object]) -> ProcessConfig:
    data_dir = raw.get("data_dir")
    paths = raw.get("paths")
    if data_dir is None and isinstance(paths, dict):
        data_dir = cast(dict[str, object], paths).get("data_dir")
    return ProcessConfig(
        data_dir=_expand_path_value(str(data_dir or ".yuubot-data")),
        python_kernels=python_kernels_config_from_raw(raw.get("python_kernels")),
        resources=resource_config_from_raw(raw.get("resources")),
    )


def load_process_config(path: str | Path) -> ProcessConfig:
    return process_config_from_raw(load_yaml_mapping(path))


def origin_for(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def deployment_for_serve(
    raw: object,
    *,
    host: str,
    port: int,
) -> DeploymentConfig:
    data = raw if isinstance(raw, dict) else {}
    base = msgspec.convert(cast(object, data), DeploymentConfig)
    origin = origin_for(host, port)
    admin_url_base = base.admin_url_base or origin
    public_url_base = base.public_url_base or origin
    return DeploymentConfig(
        server=ServerConfig(host=host, port=port),
        admin_url_base=admin_url_base,
        public_url_base=public_url_base,
        trusted_proxies=base.trusted_proxies,
        admin_auth=base.admin_auth,
    )


def load_deployment_config(path: str, *, host: str, port: int) -> DeploymentConfig:
    return deployment_for_serve(load_yaml_mapping(path), host=host, port=port)


def host_from_url_base(url_base: str) -> str:
    parsed = urlparse(url_base)
    if not parsed.hostname:
        raise ValueError(f"invalid url base: {url_base}")
    return parsed.hostname.lower()


def share_url(public_url_base: str, share_id: str, rel_path: str = "") -> str:
    base = public_url_base.rstrip("/")
    normalized = rel_path.strip().lstrip("/")
    if normalized:
        return f"{base}/s/{share_id}/{normalized}"
    return f"{base}/s/{share_id}/"


def hosts_for_url_base(url_base: str) -> frozenset[str]:
    host = host_from_url_base(url_base)
    parsed = urlparse(url_base)
    if parsed.port is None:
        return frozenset({host})
    return frozenset({host, f"{host}:{parsed.port}"})


def _expand_path_value(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))
