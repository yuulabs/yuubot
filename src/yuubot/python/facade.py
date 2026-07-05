"""Workspace facade symlinks for ipykernel workers."""

from __future__ import annotations

import shutil
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent


def prepare_facade(workspace: Path) -> Path:
    lib_dir = workspace / ".yuubot" / "facade"
    lib_dir.mkdir(parents=True, exist_ok=True)
    for name in ("yb", "yext"):
        target = lib_dir / name
        source = _SRC_ROOT / name
        if not target.exists() and source.is_dir():
            target.symlink_to(source, target_is_directory=True)
    return lib_dir


def remove_facade(workspace: Path) -> None:
    facade_dir = workspace / ".yuubot"
    if facade_dir.exists():
        shutil.rmtree(facade_dir)
