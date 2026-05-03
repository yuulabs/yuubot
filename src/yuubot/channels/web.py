"""In-process Web Chat channel adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import attrs

from yuubot.daemon.gateway import OutboundMessage


@attrs.define
class WebChatAdapter:
    """Route gateway outbound messages to active Admin WebSocket sessions."""

    channel: str = "web"
    _queues: dict[str, asyncio.Queue[str]] = attrs.field(factory=dict, init=False)

    async def start(self, emit: Any) -> None:
        return None

    async def stop(self) -> None:
        self._queues.clear()

    def bind_session(self, session_id: str, queue: asyncio.Queue[str]) -> None:
        self._queues[session_id] = queue

    def unbind_session(self, session_id: str) -> None:
        self._queues.pop(session_id, None)

    async def send(self, ctx, message: OutboundMessage) -> None:
        session_id = message.reply_to or str(getattr(ctx, "key", ""))
        queue = self._queues.get(session_id)
        if queue is None:
            return
        await queue.put(json.dumps({
            "type": "message",
            "role": "assistant",
            "text": message.text,
        }))
