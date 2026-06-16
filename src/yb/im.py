"""IM response helpers for actor Python sessions."""

from __future__ import annotations

import msgspec

from yuubot.core.facade.protocol import FacadeRpcRequest, ImResponsePayload
from yb import _client, _context


async def respond(text: str, *, msg_id: str | None = None) -> dict[str, object]:
    """Send a text response to an inbound integration message."""
    payload = ImResponsePayload(msg_id=msg_id or "", text=text)
    request = _im_request(payload)
    response = await _client.request(request)
    return response.result


async def react(emoji: str, *, msg_id: str | None = None) -> dict[str, object]:
    """Send a quick reaction to an inbound integration message."""
    payload = ImResponsePayload(msg_id=msg_id or "", react=emoji)
    request = _im_request(payload)
    response = await _client.request(request)
    return response.result


def _im_request(payload: ImResponsePayload) -> FacadeRpcRequest:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return FacadeRpcRequest(
        token=bridge.token,
        kind="im_response",
        actor_id=actor.actor_id,
        agent_name=actor.agent_name,
        session_id=actor.session_id,
        mailbox_id=actor.mailbox_id,
        payload=msgspec.to_builtins(payload),
    )