"""Local maintenance handlers for the Admin process."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class MaintenanceCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[
    [Sequence[str], Path],
    Awaitable[MaintenanceCommandResult],
]
RestartRequester = Callable[[], None]


def make_update_service_handler(
    *,
    repo_root: Path | None = None,
    command_runner: CommandRunner | None = None,
    restart_requester: RestartRequester | None = None,
):
    runner = command_runner or _run_command
    request_restart = restart_requester or _request_parent_restart

    async def update_service(_: Request) -> JSONResponse:
        if restart_requester is None and not _has_parent_restart_target():
            return JSONResponse(
                {
                    "status": "error",
                    "detail": "service restart requires ybot dev",
                },
                status_code=409,
            )

        root = repo_root or _find_repo_root(Path.cwd()) or _find_repo_root(Path(__file__))
        if root is None:
            return JSONResponse(
                {
                    "status": "error",
                    "detail": "repository root not found",
                },
                status_code=500,
            )

        steps: list[dict[str, object]] = []
        for argv in (("git", "pull", "--ff-only"), ("uv", "sync")):
            result = await runner(argv, root)
            step = _result_payload(result)
            steps.append(step)
            if result.returncode != 0:
                return JSONResponse(
                    {
                        "status": "error",
                        "detail": f"{argv[0]} failed with code {result.returncode}",
                        "data": {"steps": steps},
                    },
                    status_code=500,
                )

        return JSONResponse(
            {
                "status": "ok",
                "detail": "updated; restarting service",
                "data": {"steps": steps},
            },
            background=BackgroundTask(_call_restart, request_restart),
        )

    return update_service


async def _call_restart(restart_requester: RestartRequester) -> None:
    restart_requester()


async def _run_command(
    argv: Sequence[str],
    cwd: Path,
    *,
    timeout_s: float = 300.0,
) -> MaintenanceCommandResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_s,
        )
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return MaintenanceCommandResult(
            argv=tuple(argv),
            returncode=124,
            stdout=_decode(stdout),
            stderr=_decode(stderr) + "\ncommand timed out",
        )
    return MaintenanceCommandResult(
        argv=tuple(argv),
        returncode=process.returncode,
        stdout=_decode(stdout),
        stderr=_decode(stderr),
    )


def _request_parent_restart() -> None:
    raw_pid = os.environ.get("YUUBOT_DEV_SUPERVISOR_PID", "")
    if not raw_pid:
        return
    parent_pid = int(raw_pid)
    restart_signal = getattr(signal, "SIGHUP", signal.SIGTERM)
    os.kill(parent_pid, restart_signal)


def _has_parent_restart_target() -> bool:
    raw_pid = os.environ.get("YUUBOT_DEV_SUPERVISOR_PID", "")
    if not raw_pid:
        return False
    try:
        parent_pid = int(raw_pid)
    except ValueError:
        return False
    return parent_pid > 1


def _find_repo_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        if (path / "uv.lock").is_file() and (path / ".git").exists():
            return path
    return None


def _result_payload(result: MaintenanceCommandResult) -> dict[str, object]:
    return {
        "command": list(result.argv),
        "returncode": result.returncode,
        "stdout": _truncate(result.stdout),
        "stderr": _truncate(result.stderr),
    }


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip()


def _truncate(value: str, *, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]
