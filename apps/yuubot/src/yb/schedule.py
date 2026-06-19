"""Cron scheduling helpers for actor Python sessions."""

from __future__ import annotations

from collections.abc import Iterable

from yuubot.core.facade.protocol import FacadeRpcRequest
from yb import _client, _context


async def create_cron(
    cron: str,
    actions: Iterable[str],
    *,
    job_id: str | None = None,
    once: bool = False,
) -> str:
    """Create a cron job using yuuagents schedule actions."""
    return await _schedule_tool(
        "create_cron",
        {
            "cron": cron,
            "actions": tuple(actions),
            "job_id": job_id or "",
            "once": once,
        },
    )


async def create(
    cron: str,
    prompt: str,
    *,
    job_id: str | None = None,
    once: bool = False,
    agent_name: str | None = None,
) -> str:
    """Create a cron job that sends a prompt to an actor agent."""
    target_agent = agent_name or _context.actor_context().agent_name
    return await create_cron(
        cron,
        (f"agent:{target_agent}:{prompt}",),
        job_id=job_id,
        once=once,
    )


async def list_crons() -> str:
    """List actor cron jobs and recent trigger history."""
    return await _schedule_tool("list_crons", {})


async def delete_cron(job_id: str) -> str:
    """Delete an actor cron job."""
    return await _schedule_tool("delete_cron", {"job_id": job_id})


async def _schedule_tool(tool_name: str, payload: dict[str, object]) -> str:
    response = await _client.request(_schedule_request(tool_name, payload))
    result = response.result
    if isinstance(result, dict):
        return str(result.get("output") or "")
    return ""


def _schedule_request(tool_name: str, payload: dict[str, object]) -> FacadeRpcRequest:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return FacadeRpcRequest(
        token=bridge.token,
        kind="schedule",
        actor_id=actor.actor_id,
        agent_name=actor.agent_name,
        session_id=actor.session_id,
        mailbox_id=actor.mailbox_id,
        capability_id=tool_name,
        payload=payload,
    )