"""Operations functions exposed to RFC2 Python sessions."""

from __future__ import annotations

import asyncio
import re
from typing import Literal, TypedDict, cast

from yuubot.agent_fns.context import current_session_state
from yuubot.agent_fns.local import service_payload
from yuubot.services.workspace import WorkspaceService

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mKJHABCDfsu]")

__all__ = ["bash", "health", "recent_logs", "run_check"]


class HealthResult(TypedDict, total=False):
    ctx_id: int | None
    bot_kind: str
    agent_name: str | None
    workspace_root: str | None
    daemon_base_url: str | None
    recorder_base_url: str | None


class RunCheckResult(TypedDict):
    status: Literal["ok", "error", "timeout"]
    returncode: int | None
    stdout: str
    stderr: str


async def bash(command: str, *, timeout: float = 60.0) -> str:
    """Run a shell command in the session workspace and return combined stdout plus stderr text."""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[timed out after {timeout}s]"
    out = _ANSI_ESCAPE.sub("", stdout.decode(errors="replace"))
    err = _ANSI_ESCAPE.sub("", stderr.decode(errors="replace"))
    return out + err if err else out


def health() -> HealthResult:
    """Return current session ids, bot kind, workspace root, and daemon/recorder base URLs."""
    s = current_session_state()
    return {
        "ctx_id": s.ctx_id,
        "bot_kind": s.bot_kind or "group",
        "agent_name": _optional_str(s.agent_name),
        "workspace_root": _optional_str(s.workspace_root),
        "daemon_base_url": _optional_str(s.daemon_base_url),
        "recorder_base_url": _optional_str(s.recorder_base_url),
    }


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


async def recent_logs(component: str = "daemon", *, limit: int = 100) -> str:
    """Read recent daemon/recorder/napcat log text visible from the workspace logs directory."""
    filename = "daemon.log" if component not in {"recorder", "napcat"} else f"{component}.log"
    return await WorkspaceService().read_file(
        service_payload(path=f"logs/{filename}", max_chars=max(1000, limit * 200))
    )


async def run_check(command: str) -> RunCheckResult:
    """Run an allowlisted operational command and return status, return code, stdout, and stderr."""
    return cast(
        RunCheckResult,
        await WorkspaceService().run_command(service_payload(command=command)),
    )
