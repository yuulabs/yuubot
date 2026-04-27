"""Schedule functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def create_schedule(task: str, cron: str, *, once: bool = False) -> dict[str, Any]:
    """Create a reminder or scheduled agent task in the current context."""
    return await _p.call("schedule", "create", task=task, cron=cron, once=once)


async def list_schedules() -> list[dict[str, Any]]:
    """List schedules visible to the current actor."""
    return await _p.call("schedule", "list")


async def cancel_schedule(schedule_id: int | str) -> dict[str, Any]:
    """Cancel a schedule visible to the current actor."""
    return await _p.call("schedule", "cancel", schedule_id=schedule_id)
