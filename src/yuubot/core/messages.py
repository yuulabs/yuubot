"""Gateway inbound message contracts."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import msgspec

if TYPE_CHECKING:
    from yuullm.types import ContentItem

SYSTEM_SOURCE_PREFIX = "system:"


class MessageSource(msgspec.Struct):
    """Stable source identity for Gateway routing and actor-visible metadata."""

    producer: str = "integration"
    id: str = ""
    path: str = ""


class IncomingMessage(msgspec.Struct):
    """Base message type routed through Gateway.

    content holds yuullm ContentItem dicts (TextItem, ImageItem, etc.)
    directly, avoiding an unnecessary intermediate Segment type.
    """

    message_id: str
    sender_id: str
    source: MessageSource = msgspec.field(default_factory=MessageSource)
    kind: str = ""
    sender_name: str = ""
    # ContentItem dicts (TextItem / ImageItem / AudioItem / FileItem);
    # msgspec does not support TypedDict unions, so we use dict[str, object].
    content: list[dict[str, object]] = msgspec.field(default_factory=list)
    timestamp: int = msgspec.field(default_factory=lambda: int(time.time()))

    def render_metadata(self) -> str:
        """Render sender identity as a human-readable prefix for the LLM."""
        name = self.sender_name or self.sender_id
        ts = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
        return f"[{name} {ts}] "

    def content_items(self) -> list[ContentItem]:
        """Typed accessor for content cast to yuullm ContentItem list.

        The underlying field is list[dict[str, object]] because msgspec does
        not support TypedDict unions, but the dicts are ContentItem-shaped.
        This method centralizes the cast behind a named API.
        """
        return cast("list[ContentItem]", self.content)


def system_source_id(actor_id: str) -> str:
    return f"{SYSTEM_SOURCE_PREFIX}{actor_id}"


def system_source_for_actor(actor_id: str, *, path: str = "") -> MessageSource:
    return MessageSource(
        producer="system",
        id=system_source_id(actor_id),
        path=path,
    )
