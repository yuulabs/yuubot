"""Workspace preparation for ipykernel workers."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .facade import prepare_facade

_RUNTIME_SOURCE = Path(__file__).with_name("worker_runtime.py")
_PYPROJECT_SOURCE = Path(__file__).with_name("workspace.pyproject.toml")


def prepare_kernel_workspace(workspace: Path) -> Path:
    root = workspace.resolve()
    yuubot_dir = root / ".yuubot"
    yuubot_dir.mkdir(parents=True, exist_ok=True)
    prepare_facade(root)
    runtime_target = yuubot_dir / "worker_runtime.py"
    shutil.copy2(_RUNTIME_SOURCE, runtime_target)
    return yuubot_dir


async def ensure_workspace_venv(workspace: Path) -> Path:
    root = workspace.resolve()
    python = root / ".venv" / "bin" / "python"
    if python.is_file():
        return python
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        shutil.copy2(_PYPROJECT_SOURCE, pyproject)
    process = await asyncio.create_subprocess_exec(
        "uv",
        "sync",
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip() or f"exit {process.returncode}"
        raise RuntimeError(f"uv sync failed for workspace {root}: {detail}")
    if not python.is_file():
        raise RuntimeError(f"workspace venv python missing after uv sync: {python}")
    return python
