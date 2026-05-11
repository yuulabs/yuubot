"""Gateway inbound message contracts."""

from __future__ import annotations

import time
from typing import Literal

import msgspec

SegmentKind = Literal["text", "image", "file"]
SYSTEM_SOURCE_PREFIX = "system:"


class Segment(msgspec.Struct):
    kind: SegmentKind
    text: str = ""
    url: str = ""
    path: str = ""


class MessageSource(msgspec.Struct):
    """Stable source identity for Gateway routing and actor-visible metadata."""

    producer: str = "integration"
    id: str = ""
    path: str = ""


class IncomingMessage(msgspec.Struct):
    """Base message type routed through Gateway."""

    message_id: str
    sender_id: str
    source: MessageSource = msgspec.field(default_factory=MessageSource)
    kind: str = ""
    sender_name: str = ""
    segments: tuple[Segment, ...] = ()
    text: str = ""
    timestamp: int = msgspec.field(default_factory=lambda: int(time.time()))


def system_source_id(actor_id: str) -> str:
    return f"{SYSTEM_SOURCE_PREFIX}{actor_id}"


def system_source_for_actor(actor_id: str, *, path: str = "") -> MessageSource:
    return MessageSource(
        producer="system",
        id=system_source_id(actor_id),
        path=path,
    )
