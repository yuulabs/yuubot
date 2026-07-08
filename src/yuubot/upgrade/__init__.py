"""Git source upgrade entrypoints."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from pathlib import Path

from .apply import schedule_apply
from .git import check_git_update
from .install import INSTALL_KIND_GIT, detect_install, project_root
from .types import UpdateApplyResult, UpdateStatus


def current_version() -> str:
    try:
        return importlib.metadata.version("yuubot")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


async def check_update(root: Path | None = None) -> UpdateStatus:
    resolved_root = root or project_root()
    supported, install_kind, message = detect_install(resolved_root)
    version = current_version()
    if not supported:
        return UpdateStatus(
            False,
            install_kind,
            version,
            message=message,
        )

    current_commit, remote_commit, update_available, git_message = await check_git_update(resolved_root)
    if git_message:
        return UpdateStatus(
            True,
            INSTALL_KIND_GIT,
            version,
            current_commit,
            remote_commit,
            False,
            git_message,
        )

    return UpdateStatus(
        True,
        INSTALL_KIND_GIT,
        version,
        current_commit,
        remote_commit,
        update_available,
        "update available" if update_available else "up to date",
    )


def apply_update(
    config_path: Path,
    data_dir: Path,
    port: int,
    skip_web_build: bool = False,
    on_shutdown: Callable[[], None] | None = None,
    root: Path | None = None,
) -> UpdateApplyResult:
    resolved_root = root or project_root()
    supported, _install_kind, message = detect_install(resolved_root)
    if not supported:
        raise ValueError(message or "upgrade is not supported for this installation")
    return schedule_apply(
        root=resolved_root,
        config_path=config_path,
        data_dir=data_dir,
        port=port,
        skip_web_build=skip_web_build,
        on_shutdown=on_shutdown,
    )


__all__ = [
    "UpdateApplyResult",
    "UpdateStatus",
    "apply_update",
    "check_update",
    "current_version",
    "project_root",
]
