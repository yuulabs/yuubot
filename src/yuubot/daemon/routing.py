"""Pure routing logic — resolve an InboundMessage to a Route."""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger

from yuubot.commands.tree import RootCommand
from yuubot.core.models import AtSegment, ReplySegment, segments_to_plain
from yuubot.core.types import CommandRoute, ConversationRoute, InboundMessage, Route


def resolve_route(
    msg: InboundMessage,
    root: RootCommand,
    has_active_conversation: Callable[[int], bool],
    is_auto: Callable[[int], bool],
    bot_qq: int,
) -> Route | None:
    """Determine how to handle an inbound message.

    Pure function: no IO, no side effects. Returns:
    - CommandRoute if a command-tree node matched
    - ConversationRoute if @bot or auto mode should trigger the LLM
    - None if the message should be ignored
    """
    bot_qq_str = str(bot_qq)

    # Detect @bot
    at_bot = any(
        isinstance(s, AtSegment) and s.qq == bot_qq_str
        for s in msg.segments
    )

    # Build plain text: filter out @bot and move ReplySegments to the end
    replies = [s for s in msg.segments if isinstance(s, ReplySegment)]
    others = [
        s for s in msg.segments
        if not isinstance(s, ReplySegment)
        and not (isinstance(s, AtSegment) and s.qq == bot_qq_str)
    ]
    plain = segments_to_plain(others + replies).strip()

    # For command matching, strip ALL @ mentions (non-bot @ can precede commands)
    cmd_segs = [s for s in others if not isinstance(s, AtSegment)]
    cmd_text = segments_to_plain(cmd_segs + replies).strip()

    # 1. Try command tree match
    cmd_match = root.match_message(cmd_text)
    if cmd_match is not None:
        return CommandRoute(
            command_path=cmd_match.command_path,
            remaining=cmd_match.remaining,
            entry=cmd_match.entry,
        )

    # 2. @bot with no command → conversation
    if at_bot:
        llm_cmd = root.find(["llm"])
        if llm_cmd and llm_cmd.executor is not None:
            return ConversationRoute(
                ctx_id=msg.ctx_id,
                agent_name="main",
                is_continuation=has_active_conversation(msg.ctx_id),
                text="continue " + plain,
            )

    # 3. Auto mode (private only) bare text → conversation
    if msg.chat_type == "private" and is_auto(msg.ctx_id):
        llm_cmd = root.find(["llm"])
        if llm_cmd and llm_cmd.executor is not None:
            return ConversationRoute(
                ctx_id=msg.ctx_id,
                agent_name="main",
                is_continuation=has_active_conversation(msg.ctx_id),
                text="continue " + plain,
            )

    logger.info("No command match: {}", plain)
    return None
