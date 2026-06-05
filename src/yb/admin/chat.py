"""Web chat history access for actors.

Usage from an agent's Python session:
    dialog = await yb.admin.chat.get_dialog("dialog-abc-123")
    print('\\n\\n'.join(dialog[-5:]))  # last 5 messages
"""

from __future__ import annotations

from typing import Any

from yb import _client, _context


async def get_dialog(dialog_id: str) -> list[str]:
    """Retrieve the full rendered message history for a dialog.

    Returns a list of pre-rendered text strings, one per message.
    Each string follows the format:
        [{message_id} {sender_name} {YYYY-MM-DD HH:MM:SS}] {text}

    Args:
        dialog_id: The dialog identifier (e.g. "dialog-abc-123").

    Returns:
        list[str]: Rendered message lines, oldest first.

    Raises:
        RuntimeError: If the bridge call fails or dialog is not found.
    """
    response = await _client.request(
        _system_request(
            "admin.chat.get_dialog",
            {"dialog_id": dialog_id},
        )
    )
    result = response.get("result", {})
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if isinstance(messages, list):
            return [str(m) for m in messages]
    return []


def _system_request(capability_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return {
        "token": bridge.token,
        "kind": "system",
        "actor_id": actor.actor_id,
        "agent_name": actor.agent_name,
        "session_id": actor.session_id,
        "mailbox_id": actor.mailbox_id,
        "capability_id": capability_id,
        "payload": payload,
    }
