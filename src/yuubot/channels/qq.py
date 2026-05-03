"""QQ channel adapter backed by the recorder/NapCat send API."""

from __future__ import annotations

from typing import Any

import attrs
import httpx

from yuubot.core.models import Message, TextSegment
from yuubot.core.onebot import build_send_msg
from yuubot.daemon.gateway import OutboundMessage


@attrs.define
class QQRecorderAdapter:
    """Send gateway outbound messages through the existing recorder API."""

    recorder_api: str
    channel: str = "qq"

    async def start(self, emit: Any) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, ctx, message: OutboundMessage) -> None:
        msg_type = _qq_msg_type(ctx)
        target_id = _qq_target_id(ctx, msg_type)
        segments: Message = list(message.segments) if message.segments else [TextSegment(text=message.text)]
        body = build_send_msg(msg_type, target_id, segments)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{self.recorder_api}/send_msg_guaranteed", json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"QQ send failed ({response.status_code}): {response.text}")


def _qq_msg_type(ctx) -> str:
    kind = str(getattr(ctx, "kind", "") or getattr(ctx, "type", ""))
    if kind in {"group", "private"}:
        return kind
    metadata = getattr(ctx, "metadata", {}) or {}
    if metadata.get("group_id"):
        return "group"
    if metadata.get("user_id"):
        return "private"
    raise ValueError(f"context {getattr(ctx, 'id', '?')} is not a QQ private/group context")


def _qq_target_id(ctx, msg_type: str) -> int:
    metadata = getattr(ctx, "metadata", {}) or {}
    key = "group_id" if msg_type == "group" else "user_id"
    raw = metadata.get(key) or getattr(ctx, "target_id", 0)
    target_id = int(raw or 0)
    if target_id <= 0:
        raise ValueError(f"context {getattr(ctx, 'id', '?')} has no QQ {key}")
    return target_id
