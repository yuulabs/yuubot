"""Pure routing logic — resolve an InboundMessage to a Route."""

from __future__ import annotations

from yuubot.auth import is_master_user
from yuubot.commands.tree import RootCommand
from yuubot.core.models import AtSegment, ReplySegment, segments_to_plain
from yuubot.core.types import CommandRoute, InboundMessage, Route


def resolve_route(
    msg: InboundMessage,
    root: RootCommand,
    bot_qq: int,
    master_id: int,
) -> Route | None:
    """Determine how to handle an inbound message.

    Pure function: no IO, no side effects. Returns:
    - CommandRoute if a command-tree node matched
    - CommandRoute(command_path=("llm",), remaining="continue <text>", entry="@")
      if @bot should trigger the Group LLM
    - CommandRoute(command_path=("llm",), remaining="continue <text>", entry="master")
      if a Master private message should trigger the Master LLM
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
    cmd_text = segments_to_plain(cmd_segs + replies).strip()  # type: ignore[invalid-argument-type]

    # 1. Try command tree match
    cmd_match = root.match_message(cmd_text)
    if cmd_match is not None:
        return CommandRoute(
            command_path=cmd_match.command_path,
            remaining=cmd_match.remaining,
            entry=cmd_match.entry,
        )

    llm_cmd = root.find(["llm"])

    # 2. Master private bare text → llm continue
    if msg.chat_type == "private" and is_master_user(msg.sender.user_id, master_id) and plain:
        if llm_cmd and llm_cmd.executor is not None:
            return CommandRoute(
                command_path=("llm",),
                remaining="continue " + plain,
                entry="master",
            )

    # 3. @bot with no command → llm continue
    if at_bot:
        if llm_cmd and llm_cmd.executor is not None:
            return CommandRoute(
                command_path=("llm",),
                remaining="continue " + plain,
                entry="@",
            )

    return None
