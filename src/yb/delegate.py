"""Task delegation helpers for actor Python sessions."""

from __future__ import annotations

import uuid
from typing import Any

from yb import _client, _context


async def submit(prompt: str, *, name: str | None = None) -> str:
    """Submit a prompt to a same-actor delegate agent and return its task id."""
    task_id = uuid.uuid4().hex
    response = await _client.request(
        _delegate_request(
            task_id,
            {
                "prompt": prompt,
                "delegate_name": name or "",
            },
        )
    )
    result = response.get("result", {})
    if isinstance(result, dict) and result.get("task_id"):
        return str(result["task_id"])
    return task_id


def _delegate_request(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return {
        "token": bridge.token,
        "kind": "delegate_submit",
        "actor_id": actor.actor_id,
        "agent_name": actor.agent_name,
        "session_id": actor.session_id,
        "mailbox_id": actor.mailbox_id,
        "task_id": task_id,
        "payload": payload,
    }
