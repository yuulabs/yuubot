"""Session workspace pool helpers for E2E tests."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from yuubot.python.workspace import ensure_workspace_venv

_PRESERVED_ROOT_NAMES = frozenset({".venv", ".yuubot", "pyproject.toml"})
_PRESERVED_YUUBOT_NAMES = frozenset({"venv.ready", "facade", "worker_runtime.py", "facade_bootstrap.py"})


def workspace_shard(workspaces: tuple[Path, Path], test_name: str) -> int:
    return hash(test_name) % 2


def reset_workspace_files(workspace: Path) -> None:
    root = workspace.resolve()
    if not root.exists():
        return
    for child in root.iterdir():
        if child.name in _PRESERVED_ROOT_NAMES:
            if child.name == ".yuubot" and child.is_dir():
                for nested in child.iterdir():
                    if nested.name not in _PRESERVED_YUUBOT_NAMES:
                        if nested.is_dir():
                            shutil.rmtree(nested)
                        else:
                            nested.unlink()
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


async def prepare_session_workspaces(base_dir: Path) -> tuple[Path, Path]:
    alpha = base_dir / "workspace-alpha"
    beta = base_dir / "workspace-beta"
    alpha.mkdir(parents=True, exist_ok=True)
    beta.mkdir(parents=True, exist_ok=True)
    await asyncio.gather(
        ensure_workspace_venv(alpha),
        ensure_workspace_venv(beta),
    )
    return alpha, beta
