"""Media path mapping between host storage and agent runtime views."""

from __future__ import annotations

from dataclasses import dataclass

from yuubot.core import env


class MediaPathError(ValueError):
    """Raised when a runtime path cannot be mapped to a host-accessible path."""


@dataclass(frozen=True)
class MediaPathContext:
    docker_host_mount: str
    host_home_dir: str
    container_home_dir: str

    @property
    def in_docker(self) -> bool:
        return bool(self.docker_host_mount)

    @classmethod
    def from_env(cls) -> "MediaPathContext":
        return cls(
            docker_host_mount=env.get(env.DOCKER_HOST_MOUNT),
            host_home_dir=env.get(env.DOCKER_HOME_HOST_DIR),
            container_home_dir=env.get(env.DOCKER_HOME_DIR),
        )

    @classmethod
    def from_values(
        cls,
        *,
        docker_host_mount: str = "",
        host_home_dir: str = "",
        container_home_dir: str = "",
    ) -> "MediaPathContext":
        return cls(
            docker_host_mount=docker_host_mount,
            host_home_dir=host_home_dir,
            container_home_dir=container_home_dir,
        )


def strip_file_uri(path_or_uri: str) -> str:
    if path_or_uri.startswith("file:///"):
        return path_or_uri[len("file://"):]
    if path_or_uri.startswith("file://"):
        return path_or_uri[len("file://"):]
    return path_or_uri


def to_file_uri(path: str) -> str:
    return path if path.startswith("file://") else f"file://{path}"


def host_to_runtime(path: str, *, ctx: MediaPathContext | None = None) -> str:
    """Project a host path into the agent-visible runtime path."""
    ctx = ctx or MediaPathContext.from_env()
    raw = strip_file_uri(path)
    if not raw or not raw.startswith("/"):
        return raw
    if not ctx.in_docker:
        return raw
    if raw.startswith(ctx.docker_host_mount.rstrip("/") + "/") or raw == ctx.docker_host_mount:
        return raw
    return f"{ctx.docker_host_mount.rstrip('/')}{raw}"


def runtime_to_host(path: str, *, ctx: MediaPathContext | None = None) -> str:
    """Convert an agent-visible path back to a host path."""
    ctx = ctx or MediaPathContext.from_env()
    raw = strip_file_uri(path)
    if not raw or not raw.startswith("/"):
        return raw

    mount = ctx.docker_host_mount.rstrip("/")
    if mount and (raw == mount or raw.startswith(mount + "/")):
        suffix = raw[len(mount):]
        return suffix or "/"

    if not ctx.in_docker:
        return raw

    container_home = ctx.container_home_dir.rstrip("/")
    host_home = ctx.host_home_dir.rstrip("/")
    if container_home and host_home and (raw == container_home or raw.startswith(container_home + "/")):
        suffix = raw[len(container_home):]
        return f"{host_home}{suffix}"

    raise MediaPathError("无法发送该图片：路径不在共享目录 ~/ 下。请先将图片保存到 ~/ 下再发送。")


def input_to_host(path_or_uri: str, *, ctx: MediaPathContext | None = None) -> str:
    """Normalize any incoming media path/URI to a host path."""
    return runtime_to_host(path_or_uri, ctx=ctx)
