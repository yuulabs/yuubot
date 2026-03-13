"""RenderPolicy — centralized message→LLM rendering logic.

Extracts rendering decisions from agent_runner._build_task(),
dispatcher._build_ping_payload(), and agent_runner._build_memory_hints()
into a composable, testable module.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import attrs
import msgspec
from loguru import logger

from yuubot.core.models import (
    AtSegment,
    Segment,
    TextSegment,
    segments_to_json,
)
from yuubot.core.onebot import parse_segments
from yuubot.core.types import InboundMessage
from yuubot.rendering import ConversationRender
from yuubot.capabilities.im.formatter import (
    format_message_to_xml,
    get_user_alias,
)


# ── Policy & Context ─────────────────────────────────────────────


class RenderPolicy(msgspec.Struct, frozen=True):
    """Declarative rendering configuration for message→LLM conversion."""

    message_format: str = "xml"  # "xml" | "plain"
    strip_bot_at: bool = True
    include_group_name: bool = True
    replace_prefix_with_bot_name: bool = True
    reply_style: str = "quote"  # "quote" | "inline" | "id_only"
    image_style: str = "local_file_uri"  # "local_file_uri" | "url" | "placeholder"
    name_priority: list[str] = msgspec.field(
        default_factory=lambda: ["alias", "display_name", "nickname", "qq"]
    )
    merge_pending: bool = True
    max_batch_size: int = 8


@attrs.define
class RenderContext:
    """Runtime context needed during rendering (not part of policy)."""

    group_name: str = ""
    bot_name: str = ""
    has_vision: bool = False
    bot_qq: str = ""


# ── Pure helpers ─────────────────────────────────────────────────


_CMD_PREFIX_RE = re.compile(r"^(/yllm|/yuu|/y)(?:#\w+)?\s*")


def replace_command_prefix(segments: list[Segment], bot_name: str) -> list[Segment]:
    """Replace /yllm command prefix with @bot_name in the first text segment.

    Handles: /yllm, /y, /yuu with optional #agent_name suffix.
    Skips leading non-text segments (e.g. ReplySegment) to find the command.
    Returns a new list with modified segments.
    """
    for i, seg in enumerate(segments):
        if not isinstance(seg, TextSegment):
            continue
        text = seg.text.strip()
        match = _CMD_PREFIX_RE.match(text)
        if match:
            new_text = f"@{bot_name} " + text[match.end() :]
            new_segments = list(segments)
            new_segments[i] = TextSegment(text=new_text)
            return new_segments
        break

    return segments


def _strip_bot_at(segments: list[Segment], bot_qq: str) -> list[Segment]:
    """Remove @bot AtSegments — redundant noise for the LLM."""
    return [s for s in segments if not (isinstance(s, AtSegment) and s.qq == bot_qq)]


def _build_location(msg: InboundMessage, group_name: str, include_name: bool) -> str:
    return ConversationRender.location(
        chat_type=msg.chat_type,
        group_id=msg.raw_event.get("group_id", "?"),
        group_name=group_name,
        ctx_id=msg.ctx_id,
        include_name=include_name,
    )


# ── Core render functions ────────────────────────────────────────


async def _render_single_msg_xml(
    event: dict,
    segments: list[Segment],
    ctx_id: int,
) -> str:
    """Render one event's segments to XML <msg> tag."""
    user_id = event.get("user_id", "?")
    nickname = event.get("sender", {}).get("nickname", "")
    display_name = event.get("sender", {}).get("card", "")
    alias = await get_user_alias(user_id, ctx_id)
    ts = datetime.fromtimestamp(event.get("time", 0), tz=timezone.utc)
    raw_json = segments_to_json(segments)

    return await format_message_to_xml(
        msg_id=event.get("message_id", 0),
        user_id=user_id,
        nickname=nickname,
        display_name=display_name,
        alias=alias,
        timestamp=ts,
        raw_message=raw_json,
        media_files=event.get("media_files", []),
        ctx_id=ctx_id,
    )


async def render_task(
    msg: InboundMessage,
    policy: RenderPolicy,
    context: RenderContext,
    *,
    is_continuation: bool = False,
    memory_hints: str = "",
) -> str:
    """Render an InboundMessage to an LLM task string.

    Absorbs agent_runner._build_task() rendering logic (text part only).
    Vision/multimodal wrapping is left to the caller.
    """
    event = msg.raw_event
    segments = list(msg.segments)

    # Strip @bot
    if policy.strip_bot_at and context.bot_qq:
        segments = _strip_bot_at(segments, context.bot_qq)

    # Replace command prefix
    if policy.replace_prefix_with_bot_name and context.bot_name:
        segments = replace_command_prefix(segments, context.bot_name)

    ctx_id = msg.ctx_id

    # Render main message XML
    msg_xml = await _render_single_msg_xml(event, segments, ctx_id)

    # Render extra (debounced) events
    extra_events = event.get("_extra_events", [])
    for extra in extra_events[: policy.max_batch_size - 1]:
        extra_segments = parse_segments(extra.get("message", []))
        if policy.strip_bot_at and context.bot_qq:
            extra_segments = _strip_bot_at(extra_segments, context.bot_qq)
        if policy.replace_prefix_with_bot_name and context.bot_name:
            extra_segments = replace_command_prefix(extra_segments, context.bot_name)
        extra_xml = await _render_single_msg_xml(extra, extra_segments, ctx_id)
        msg_xml += "\n" + extra_xml

    # Assemble final text
    if is_continuation:
        return ConversationRender.user_continuation(
            total_msgs=1 + len(extra_events),
            msg_xml=msg_xml,
            memory_hints=memory_hints,
        )

    location = _build_location(msg, context.group_name, policy.include_group_name)
    return ConversationRender.user_new(
        location=location,
        msg_xml=msg_xml,
        memory_hints=memory_hints,
        ctx_id=ctx_id,
    )


async def render_ping_payload(
    msg: InboundMessage,
    policy: RenderPolicy,
    context: RenderContext,
) -> str:
    """Render a ping payload for an active conversation.

    Absorbs dispatcher._build_ping_payload() logic.
    """
    segments = list(msg.segments)

    if policy.strip_bot_at and context.bot_qq:
        segments = _strip_bot_at(segments, context.bot_qq)
    if policy.replace_prefix_with_bot_name and context.bot_name:
        segments = replace_command_prefix(segments, context.bot_name)

    return await _render_single_msg_xml(msg.raw_event, segments, msg.ctx_id)


async def render_memory_hints(text: str, ctx_id: int | None = None) -> str:
    """Probe message text against memory FTS5, return hint string.

    Absorbs agent_runner._build_memory_hints().
    Best-effort: returns empty string on any failure.
    """
    try:
        from yuubot.capabilities.mem.store import probe_text

        hits = await probe_text(text, ctx_id=ctx_id)
        if not hits:
            return ""
        return ConversationRender.memory_hint(hits=hits)
    except Exception:
        logger.opt(exception=True).debug("Memory hints probe failed")
        return ""
