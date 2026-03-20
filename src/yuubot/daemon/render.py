"""RenderPolicy — centralized message→LLM rendering logic.

Extracts rendering decisions from agent_runner._build_task(),
dispatcher._build_ping_payload(), and agent_runner._build_memory_hints()
into a composable, testable module.
"""

from __future__ import annotations

from datetime import datetime, timezone

import attrs
import msgspec
from loguru import logger

from yuubot.core.media_paths import MediaPathContext
from yuubot.core.models import (
    AtSegment,
    Segment,
    segments_to_json,
)
from yuubot.core.types import InboundMessage
from yuubot.rendering import ConversationRender
from yuubot.capabilities.im.formatter import (
    format_message_to_xml,
    format_segments,
    get_user_alias,
    replace_command_prefix,
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
    docker_host_mount: str = ""


# ── Pure helpers ─────────────────────────────────────────────────


def _strip_bot_at(segments: list[Segment], bot_qq: str) -> list[Segment]:
    """Remove @bot AtSegments — redundant noise for the LLM."""
    return [s for s in segments if not (isinstance(s, AtSegment) and s.qq == bot_qq)]


def _build_location(msg: InboundMessage, group_name: str, include_name: bool) -> str:
    return ConversationRender.location(
        chat_type=msg.chat_type,
        group_id=msg.group_id or 0,
        group_name=group_name,
        ctx_id=msg.ctx_id,
        include_name=include_name,
    )


# ── Core render functions ────────────────────────────────────────


async def _render_single_msg_xml(
    msg: InboundMessage,
    segments: list[Segment],
    media_path_ctx: MediaPathContext | None = None,
) -> str:
    """Render one event's segments to XML <msg> tag."""
    user_id = msg.sender.user_id
    nickname = msg.sender.nickname
    display_name = msg.sender.card
    alias = await get_user_alias(user_id, msg.ctx_id)
    ts = datetime.fromtimestamp(msg.timestamp, tz=timezone.utc)
    raw_json = segments_to_json(segments)

    return await format_message_to_xml(
        msg_id=msg.message_id,
        user_id=user_id,
        nickname=nickname,
        display_name=display_name,
        alias=alias,
        timestamp=ts,
        raw_message=raw_json,
        media_files=msg.raw_event.get("media_files", []),
        ctx_id=msg.ctx_id,
        media_path_ctx=media_path_ctx,
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
    segments = list(msg.segments)

    # Strip @bot
    if policy.strip_bot_at and context.bot_qq:
        segments = _strip_bot_at(segments, context.bot_qq)

    # Replace command prefix
    if policy.replace_prefix_with_bot_name and context.bot_name:
        segments = replace_command_prefix(segments, context.bot_name)

    # Render main message XML
    media_path_ctx = MediaPathContext(
        docker_host_mount=context.docker_host_mount,
        host_home_dir="",
        container_home_dir="",
    )
    msg_xml = await _render_single_msg_xml(msg, segments, media_path_ctx)

    # Render extra (debounced) typed messages
    extra_messages = msg.extra_messages[: policy.max_batch_size - 1]
    for extra in extra_messages:
        extra_segments = list(extra.segments)
        if policy.strip_bot_at and context.bot_qq:
            extra_segments = _strip_bot_at(extra_segments, context.bot_qq)
        if policy.replace_prefix_with_bot_name and context.bot_name:
            extra_segments = replace_command_prefix(extra_segments, context.bot_name)
        extra_xml = await _render_single_msg_xml(extra, extra_segments, media_path_ctx)
        msg_xml += "\n" + extra_xml

    # Assemble final text
    if is_continuation:
        return ConversationRender.user_continuation(
            total_msgs=1 + len(extra_messages),
            msg_xml=msg_xml,
            memory_hints=memory_hints,
        )

    location = _build_location(msg, context.group_name, policy.include_group_name)
    return ConversationRender.user_new(
        location=location,
        msg_xml=msg_xml,
        memory_hints=memory_hints,
        ctx_id=msg.ctx_id,
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

    return await _render_single_msg_xml(msg, segments)


async def render_signal(
    msg: InboundMessage,
    policy: RenderPolicy,
    context: RenderContext,
) -> str:
    """Render an incoming message as a signal for a running agent.

    Uses the user_continuation template since this is a new message
    arriving in an already-active conversation.
    """
    segments = list(msg.segments)

    if policy.strip_bot_at and context.bot_qq:
        segments = _strip_bot_at(segments, context.bot_qq)
    if policy.replace_prefix_with_bot_name and context.bot_name:
        segments = replace_command_prefix(segments, context.bot_name)

    media_path_ctx = MediaPathContext(
        docker_host_mount=context.docker_host_mount,
        host_home_dir="",
        container_home_dir="",
    )
    msg_xml = await _render_single_msg_xml(msg, segments, media_path_ctx)

    probe_text = await format_segments(msg.segments)
    memory_hints = await render_memory_hints(probe_text, msg.ctx_id or None)

    return f"你收到了新消息:\n{msg_xml}\n{memory_hints}"


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
