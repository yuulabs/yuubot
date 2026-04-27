"""Media path mapping between host storage and agent runtime views."""

from __future__ import annotations

from dataclasses import dataclass

from yuubot.core import env


class MediaPathError(ValueError):
    """Raised when a runtime path cannot be mapped to a host-accessible path."""


@dataclass(frozen=True)
class MediaPathContext:
    workspace_root: str = ""
    deployment_mode: str = ""

    @classmethod
    def from_env(cls) -> "MediaPathContext":
        return cls(
            workspace_root=env.get(env.WORKSPACE_ROOT),
            deployment_mode=env.get(env.DEPLOYMENT_MODE),
        )

    @classmethod
    def from_values(
        cls,
        *,
        workspace_root: str = "",
        deployment_mode: str = "",
    ) -> "MediaPathContext":
        return cls(
            workspace_root=workspace_root,
            deployment_mode=deployment_mode,
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
    """Return the real runtime-visible path in the active deployment."""
    del ctx
    raw = strip_file_uri(path)
    return raw


def runtime_to_host(path: str, *, ctx: MediaPathContext | None = None) -> str:
    """Normalize an agent-visible path to the local runtime filesystem."""
    del ctx
    raw = strip_file_uri(path)
    return raw


def input_to_host(path_or_uri: str, *, ctx: MediaPathContext | None = None) -> str:
    """Normalize any incoming media path/URI to a host path."""
    return runtime_to_host(path_or_uri, ctx=ctx)
