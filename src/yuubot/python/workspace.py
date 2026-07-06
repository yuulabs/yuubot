"""Workspace preparation for ipykernel workers."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path

from .facade import prepare_facade

_RUNTIME_SOURCE = Path(__file__).with_name("worker_runtime.py")
_PYPROJECT_SOURCE = Path(__file__).with_name("workspace.pyproject.toml")
WORKSPACE_SYNC_TIMEOUT_S = 120.0
_WORKSPACE_LOCKS: dict[Path, asyncio.Lock] = {}


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
    async with _workspace_lock(root):
        python = root / ".venv" / "bin" / "python"
        ready = _venv_ready_marker(root)
        if python.is_file() and ready.is_file():
            return python
        ready.unlink(missing_ok=True)
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
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=WORKSPACE_SYNC_TIMEOUT_S)
        except TimeoutError as exc:
            ready.unlink(missing_ok=True)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            stdout, stderr = await process.communicate()
            detail = _process_output_detail(stdout, stderr) or "no output captured"
            raise RuntimeError(f"uv sync timed out after {int(WORKSPACE_SYNC_TIMEOUT_S)}s for workspace {root}: {detail}") from exc
        except asyncio.CancelledError:
            ready.unlink(missing_ok=True)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
            raise
        if process.returncode != 0:
            ready.unlink(missing_ok=True)
            detail = _process_output_detail(stdout, stderr) or f"exit {process.returncode}"
            raise RuntimeError(f"uv sync failed for workspace {root}: {detail}")
        if not python.is_file():
            ready.unlink(missing_ok=True)
            raise RuntimeError(f"workspace venv python missing after uv sync: {python}")
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text("ok\n", encoding="utf-8")
        return python


def workspace_venv_ready(workspace: Path) -> bool:
    root = workspace.resolve()
    python = root / ".venv" / "bin" / "python"
    return python.is_file() and _venv_ready_marker(root).is_file()


def _venv_ready_marker(root: Path) -> Path:
    return root / ".yuubot" / "venv.ready"


def _workspace_lock(root: Path) -> asyncio.Lock:
    lock = _WORKSPACE_LOCKS.get(root)
    if lock is None:
        lock = asyncio.Lock()
        _WORKSPACE_LOCKS[root] = lock
    return lock


def _process_output_detail(stdout: bytes, stderr: bytes) -> str:
    stderr_text = stderr.decode(errors="replace").strip()
    stdout_text = stdout.decode(errors="replace").strip()
    if stderr_text and stdout_text:
        return f"stderr: {stderr_text}\nstdout: {stdout_text}"
    if stderr_text:
        return f"stderr: {stderr_text}"
    if stdout_text:
        return f"stdout: {stdout_text}"
    return ""
