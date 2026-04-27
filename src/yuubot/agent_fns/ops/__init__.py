"""Operations functions exposed to RFC2 Python sessions."""

from __future__ import annotations

import asyncio
from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def bash(command: str, *, timeout: float = 60.0) -> str:
    """Run a shell command in the workspace directory. Returns stdout+stderr."""
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
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    return out + err if err else out


def health() -> dict[str, Any]:
    """Return daemon, recorder, and runtime health details."""
    try:
        from yuuagents.kernel import get_session_state
        s = get_session_state()
    except Exception:
        s: Any = {}
    return {
        "ctx_id": s.get("ctx_id"),
        "bot_kind": s.get("bot_kind", "group"),
        "agent_name": s.get("agent_name"),
        "workspace_root": s.get("workspace_root"),
        "daemon_base_url": s.get("daemon_base_url"),
        "recorder_base_url": s.get("recorder_base_url"),
    }


async def recent_logs(component: str = "daemon", *, limit: int = 100) -> str:
    """Read recent operational logs visible to the current actor."""
    filename = "daemon.log" if component not in {"recorder", "napcat"} else f"{component}.log"
    return await _p.call(
        "workspace",
        "read_file",
        path=f"logs/{filename}",
        max_chars=max(1000, limit * 200),
    )


async def run_check(command: str) -> dict[str, Any]:
    """Run an allowlisted operational check."""
    return await _p.call("workspace", "run_command", command=command)
