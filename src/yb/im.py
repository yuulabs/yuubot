"""IM response helpers for actor Python sessions."""

from __future__ import annotations

from typing import Any

from yb import _client, _context


async def respond(text: str, *, msg_id: str | None = None) -> dict[str, Any]:
    """Send a text response to an inbound integration message."""
    response = await _client.request(
        _im_request({"msg_id": msg_id or "", "text": text})
    )
    result = response.get("result", {})
    return result if isinstance(result, dict) else {}


async def react(emoji: str, *, msg_id: str | None = None) -> dict[str, Any]:
    """Send a quick reaction to an inbound integration message."""
    response = await _client.request(
        _im_request({"msg_id": msg_id or "", "react": emoji})
    )
    result = response.get("result", {})
    return result if isinstance(result, dict) else {}


def _im_request(payload: dict[str, Any]) -> dict[str, Any]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return {
        "token": bridge.token,
        "kind": "im_response",
        "actor_id": actor.actor_id,
        "agent_name": actor.agent_name,
        "session_id": actor.session_id,
        "mailbox_id": actor.mailbox_id,
        "payload": payload,
    }
