"""Task delegation helpers for actor Python sessions."""

from __future__ import annotations

import uuid

import msgspec

from yuubot.core.facade.protocol import DelegateSubmitPayload, FacadeRpcRequest
from yb import _client, _context


async def submit(prompt: str, *, name: str | None = None) -> str:
    """Submit a prompt to a same-actor delegate agent and return its task id."""
    task_id = uuid.uuid4().hex
    payload = DelegateSubmitPayload(prompt=prompt, delegate_name=name or "")
    request = _delegate_request(task_id, payload)
    response = await _client.request(request)
    result = response.result
    if isinstance(result, dict) and result.get("task_id"):
        return str(result["task_id"])
    return task_id


def _delegate_request(task_id: str, payload: DelegateSubmitPayload) -> FacadeRpcRequest:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return FacadeRpcRequest(
        token=bridge.token,
        kind="delegate_submit",
        actor_id=actor.actor_id,
        agent_name=actor.agent_name,
        session_id=actor.session_id,
        mailbox_id=actor.mailbox_id,
        task_id=task_id,
        payload=msgspec.to_builtins(payload),
    )