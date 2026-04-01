"""Shared domain types for the yuubot refactoring.

These typed models replace raw dicts at module boundaries:
- Sender replaces event["sender"] dict
- InboundMessage replaces raw event dict for business logic
- CommandRoute is the single routing result (@bot and auto mode resolve to llm command)
- ContentBlock tagged union for structured LLM output blocks
"""

from __future__ import annotations

from typing import Literal

import msgspec

from yuubot.core.models import Segment


# ── Sender ───────────────────────────────────────────────────────


class Sender(msgspec.Struct, frozen=True):
    """Typed sender extracted from OneBot event."""

    user_id: int
    nickname: str = ""
    card: str = ""  # group card (群名片)
    role: str = ""  # group role from OneBot: "owner" / "admin" / "member"


# ── InboundMessage ───────────────────────────────────────────────


class InboundMessage(msgspec.Struct):
    """Business-layer incoming message, converted from OneBot event.

    Carries typed fields for all downstream consumers (routing, rendering,
    conversation). ``raw_event`` preserves the original dict for gradual
    migration — code that still reads event dicts can use it.
    """

    message_id: int
    ctx_id: int
    chat_type: Literal["private", "group"]
    sender: Sender
    segments: list[Segment]
    timestamp: int
    db_id: int = 0
    raw_event: dict = msgspec.field(default_factory=dict)  # original OneBot dict, for transition
    group_id: int = 0
    self_id: int = 0
    raw_message: str = ""
    extra_messages: list["InboundMessage"] = msgspec.field(default_factory=list)


# ── Route ────────────────────────────────────────────────────────


class CommandRoute(msgspec.Struct, frozen=True):
    """A message matched a command-tree node.

    @bot and auto-mode messages resolve to CommandRoute(command_path=("llm",),
    remaining="continue <text>", entry="@") — no separate ConversationRoute type.
    """

    command_path: tuple[str, ...]  # matched command path, e.g. ("bot", "on")
    remaining: str  # text after command
    entry: str  # which entry prefix was used; "@" for @bot / auto-mode


Route = CommandRoute


# ── ContentBlock (tagged union for structured LLM output) ────────


class TextBlock(msgspec.Struct, tag="text"):
    text: str


class ImageBlock(msgspec.Struct, tag="image"):
    url: str = ""
    file: str = ""


ContentBlock = TextBlock | ImageBlock
