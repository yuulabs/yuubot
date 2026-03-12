"""Shared domain types for the yuubot refactoring.

These typed models replace raw dicts at module boundaries:
- Sender replaces event["sender"] dict
- InboundMessage replaces raw event dict for business logic
- CommandRoute / ConversationRoute replace ad-hoc routing decisions
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
    raw_event: dict  # original OneBot dict, for transition


# ── Route ────────────────────────────────────────────────────────


class CommandRoute(msgspec.Struct, frozen=True):
    """A message matched a command-tree node."""

    command: str  # matched command prefix, e.g. "bot", "help"
    remaining: str  # text after command
    entry: str  # which entry prefix was used


class ConversationRoute(msgspec.Struct, frozen=True):
    """A message should enter the conversation/LLM system."""

    ctx_id: int
    agent_name: str
    is_continuation: bool  # True if continuing an existing conversation
    text: str  # user text to send to LLM


Route = CommandRoute | ConversationRoute


# ── ContentBlock (tagged union for structured LLM output) ────────


class TextBlock(msgspec.Struct, tag="text"):
    text: str


class ImageBlock(msgspec.Struct, tag="image"):
    url: str = ""
    file: str = ""


ContentBlock = TextBlock | ImageBlock
