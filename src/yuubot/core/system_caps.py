"""System capability handler for non-integration capabilities (e.g. yb.admin.*)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yuubot.core.chat_store import ChatStore


@dataclass
class SystemCapHandler:
    """Handles system-level capability calls dispatched by kind="system"."""

    chat_store: ChatStore | None = None

    async def handle(
        self,
        capability_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str = "",
    ) -> dict[str, object]:
        """Dispatch a system capability call. Returns a result dict."""
        if capability_id == "admin.chat.get_dialog":
            return await self._get_dialog(payload)
        raise LookupError(
            f"unknown system capability: {capability_id!r}"
        )

    async def _get_dialog(
        self, payload: dict[str, Any]
    ) -> dict[str, object]:
        dialog_id = payload.get("dialog_id", "")
        if not dialog_id:
            raise ValueError("dialog_id is required")

        if self.chat_store is None:
            raise RuntimeError("chat store is not available")

        result = await self.chat_store.browse_messages(
            dialog_id,
            limit=1000,  # reasonable max for agent context
        )
        # Return as list[str] — pre-rendered text_content
        messages: list[str] = [
            m.text_content for m in result.messages
        ]
        return {
            "dialog_id": dialog_id,
            "messages": messages,
        }
