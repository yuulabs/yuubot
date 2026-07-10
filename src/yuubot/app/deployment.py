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


class ListenerConfig(msgspec.Struct, frozen=True):
    enabled: bool = False
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    url_base: str = ""


class AdminAuthBuiltinConfig(msgspec.Struct, frozen=True):
    session_cookie_name: str = "yuubot_session"
    csrf_header: str = "X-CSRF-Token"
    username: str = "admin"
    password: str = ""


class AdminAuthProxyConfig(msgspec.Struct, frozen=True):
    user_header: str = "X-Forwarded-User"
    groups_header: str | None = "X-Forwarded-Groups"


class AdminAuthConfig(msgspec.Struct, frozen=True):
    mode: Literal["proxy", "builtin", "loopback_bypass"] = "loopback_bypass"
    builtin: AdminAuthBuiltinConfig = msgspec.field(default_factory=AdminAuthBuiltinConfig)
    proxy: AdminAuthProxyConfig = msgspec.field(default_factory=AdminAuthProxyConfig)


class TrustedAdminListenerConfig(msgspec.Struct, frozen=True):
    enabled: bool = False
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    url_base: str = ""
    auth: AdminAuthConfig = msgspec.field(default_factory=AdminAuthConfig)


class DeploymentConfig(msgspec.Struct, frozen=True):
    server: ServerConfig = msgspec.field(default_factory=ServerConfig)
    surface: Literal["local_dev", "local_admin", "trusted_admin", "public"] = "local_dev"
    public_server: ListenerConfig = msgspec.field(default_factory=ListenerConfig)
    local_admin_server: ListenerConfig = msgspec.field(
        default_factory=lambda: ListenerConfig(True, DEFAULT_HOST, DEFAULT_PORT)
    )
    trusted_admin_server: TrustedAdminListenerConfig = msgspec.field(default_factory=TrustedAdminListenerConfig)
    admin_url_base: str = ""
    public_url_base: str = ""
    trusted_proxies: tuple[str, ...] = ()
    admin_auth: AdminAuthConfig = msgspec.field(default_factory=AdminAuthConfig)


class ProcessConfig(msgspec.Struct, frozen=True):
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
        _expand_path_value(str(data_dir or ".yuubot-data")),
        python_kernels_config_from_raw(raw.get("python_kernels")),
        resource_config_from_raw(raw.get("resources")),
    )


def load_process_config(path: str | Path) -> ProcessConfig:
    return process_config_from_raw(load_yaml_mapping(path))


def origin_for(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def deployment_for_serve(
    raw: object,
    host: str,
    port: int,
) -> DeploymentConfig:
    data = msgspec.convert(raw, dict[str, object]) if isinstance(raw, dict) else {}
    base = msgspec.convert(cast(object, data), DeploymentConfig)
    origin = origin_for(host, port)
    admin_url_base = base.admin_url_base or origin
    public_url_base = base.public_url_base or origin
    return DeploymentConfig(
        ServerConfig(host, port),
        "local_dev",
        admin_url_base=admin_url_base,
        public_url_base=public_url_base,
        trusted_proxies=base.trusted_proxies,
        admin_auth=base.admin_auth,
    )


def load_deployment_config(path: str, host: str, port: int) -> DeploymentConfig:
    return deployment_for_serve(load_yaml_mapping(path), host, port)


def deployment_listeners_for_serve(
    raw: object,
    host: str,
    port: int,
) -> tuple[DeploymentConfig, ...]:
    data = raw if isinstance(raw, dict) else {}
    trusted_proxies = _trusted_proxies(data.get("trusted_proxies"))
    local = _local_admin_listener_config(
        data.get("local_admin_server"),
        True,
        port,
    )
    public = _listener_config(
        data.get("public_server"),
        False,
        host,
        port,
    )
    trusted = _trusted_listener_config(
        data.get("trusted_admin_server"),
        False,
        host,
        port,
    )
    default_public_base = public.url_base or local.url_base
    deployments: list[DeploymentConfig] = []
    if local.enabled:
        origin = local.url_base or origin_for(local.host, local.port)
        deployments.append(
            DeploymentConfig(
                ServerConfig(local.host, local.port),
                "local_admin",
                public,
                local,
                trusted,
                origin,
                default_public_base or origin,
                trusted_proxies,
                AdminAuthConfig("loopback_bypass"),
            )
        )
    if public.enabled:
        origin = public.url_base or origin_for(public.host, public.port)
        deployments.append(
            DeploymentConfig(
                ServerConfig(public.host, public.port),
                "public",
                public,
                local,
                trusted,
                local.url_base or "",
                origin,
                trusted_proxies,
                AdminAuthConfig("loopback_bypass"),
            )
        )
    if trusted.enabled:
        if trusted.auth.mode == "loopback_bypass":
            raise ValueError("trusted_admin_server.auth.mode must be builtin or proxy")
        _validate_trusted_admin_auth(trusted.auth)
        origin = trusted.url_base or origin_for(trusted.host, trusted.port)
        deployments.append(
            DeploymentConfig(
                ServerConfig(trusted.host, trusted.port),
                "trusted_admin",
                public,
                local,
                trusted,
                origin,
                default_public_base or origin,
                trusted_proxies,
                trusted.auth,
            )
        )
    if not deployments:
        raise ValueError("at least one listener must be enabled")
    return tuple(deployments)


def load_listener_deployments(path: str, host: str, port: int) -> tuple[DeploymentConfig, ...]:
    return deployment_listeners_for_serve(load_yaml_mapping(path), host, port)


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


def _listener_config(
    raw: object,
    default_enabled: bool,
    default_host: str,
    default_port: int,
) -> ListenerConfig:
    if not isinstance(raw, dict):
        return ListenerConfig(default_enabled, default_host, default_port)
    return msgspec.convert(
        _listener_raw(cast(dict[str, object], raw), default_host, default_port),
        ListenerConfig,
    )


def _local_admin_listener_config(
    raw: object,
    default_enabled: bool,
    default_port: int,
) -> ListenerConfig:
    base = _listener_config(
        raw,
        default_enabled,
        DEFAULT_HOST,
        default_port,
    )
    return ListenerConfig(
        base.enabled,
        DEFAULT_HOST,
        base.port,
        "",
    )


def _trusted_listener_config(
    raw: object,
    default_enabled: bool,
    default_host: str,
    default_port: int,
) -> TrustedAdminListenerConfig:
    if not isinstance(raw, dict):
        return TrustedAdminListenerConfig(default_enabled, default_host, default_port)
    data = cast(dict[str, object], raw)
    auth_raw = data.get("auth")
    auth = msgspec.convert(auth_raw if isinstance(auth_raw, dict) else {}, AdminAuthConfig)
    base = msgspec.convert(_listener_raw(data, default_host, default_port), ListenerConfig)
    return TrustedAdminListenerConfig(
        base.enabled,
        base.host,
        base.port,
        base.url_base,
        auth,
    )


def _listener_raw(raw: dict[str, object], default_host: str, default_port: int) -> dict[str, object]:
    merged = dict(raw)
    merged.setdefault("enabled", True)
    merged.setdefault("host", default_host)
    port = merged.get("port")
    if port is None or port == "":
        merged["port"] = default_port
    else:
        merged["port"] = int(str(port))
    merged.setdefault("url_base", "")
    return merged


def _trusted_proxies(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return msgspec.convert(raw, tuple[str, ...])


def _validate_trusted_admin_auth(auth: AdminAuthConfig) -> None:
    if auth.mode == "builtin" and not auth.builtin.username.strip():
        raise ValueError("trusted_admin_server.auth.builtin.username must be set")
    if auth.mode == "builtin" and not auth.builtin.password.strip():
        raise ValueError("trusted_admin_server.auth.builtin.password must be set")
