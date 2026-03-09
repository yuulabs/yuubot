"""Format messages to LLM-readable XML."""

import html
import os
from datetime import datetime, timezone

from yuubot.core.models import (
    ImageSegment,
    Message,
    MessageRecord,
    ReplySegment,
    segments_from_json,
    segments_to_plain,
    UserAlias,
)


def _docker_host_path(path: str) -> str:
    """Prefix a host path with the Docker mount point when running inside Docker."""
    mount = os.environ.get("YUU_DOCKER_HOST_MOUNT", "")
    if mount and not path.startswith(mount):
        return f"{mount}{path}"
    return path


async def get_user_alias(user_id: int, ctx_id: int | None = None) -> str | None:
    """Get user alias for a given user_id and context."""
    # Try context-specific alias first
    if ctx_id is not None:
        alias = await UserAlias.filter(user_id=user_id, scope=f"ctx_{ctx_id}").first()
        if alias:
            return alias.alias

    # Fall back to global alias
    alias = await UserAlias.filter(user_id=user_id, scope="global").first()
    return alias.alias if alias else None


async def _resolve_at_name(qq: str, ctx_id: int | None = None) -> str:
    """Resolve an @mention QQ number to a display name.

    Tries alias first, then nickname from message history, falls back to QQ number.
    """
    try:
        uid = int(qq)
    except ValueError:
        return qq

    alias = await get_user_alias(uid, ctx_id)
    if alias:
        return alias

    record = await MessageRecord.filter(user_id=uid).order_by("-id").first()
    if record:
        return record.display_name or record.nickname or qq

    return qq


async def format_segments(
    segments: Message,
    media_files: list[str] | None = None,
    ctx_id: int | None = None,
) -> str:
    """Format segments to LLM-readable inline content.

    Handles text, images, @mentions, and reply expansion (one level).
    Use this wherever LLM needs to read message content.
    """
    if media_files is None:
        media_files = []

    parts: list[str] = []
    media_idx = 0

    for seg in segments:
        if hasattr(seg, 'text'):  # TextSegment
            parts.append(html.escape(seg.text))
        elif isinstance(seg, ImageSegment):
            if seg.local_path:
                url = f"file://{_docker_host_path(seg.local_path)}"
            elif media_idx < len(media_files):
                url = f"file://{_docker_host_path(media_files[media_idx])}"
                media_idx += 1
            elif seg.url:
                url = seg.url
            else:
                url = ""

            if url:
                parts.append(f'<image url="{html.escape(url)}"/>')
            else:
                parts.append('[图片]')
        elif hasattr(seg, 'qq'):  # AtSegment
            name = await _resolve_at_name(seg.qq, ctx_id)
            parts.append(f'@{html.escape(name)}')
        elif isinstance(seg, ReplySegment):
            reply_tag = await _format_reply(seg.id)
            parts.append(reply_tag)

    return ''.join(parts)


async def format_message_to_xml(
    msg_id: int,
    user_id: int,
    nickname: str | None,
    display_name: str | None,
    alias: str | None,
    timestamp: datetime | str,
    raw_message: str,
    media_files: list[str],
    ctx_id: int | None = None,
) -> str:
    """Format a single message to LLM-readable XML.

    Format: <msg id=X qq=Y name="..." display_name="..." alias="..." time="...">content</msg>
    """
    segments = segments_from_json(raw_message)
    content = await format_segments(segments, media_files, ctx_id=ctx_id)

    # Build attributes — use "qq" so LLM can directly reuse it in at segments
    attrs = [f'id="{msg_id}"', f'qq="{user_id}"']

    if nickname:
        attrs.append(f'name="{html.escape(nickname)}"')

    if display_name:
        attrs.append(f'display_name="{html.escape(display_name)}"')

    if alias:
        attrs.append(f'alias="{html.escape(alias)}"')

    # Format to minute precision with timezone
    if isinstance(timestamp, datetime):
        ts = timestamp
    else:
        ts = datetime.fromisoformat(str(timestamp))

    # Ensure timezone-aware (assume UTC if naive)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    time_str = ts.strftime("%Y-%m-%d %H:%M %z")
    attrs.append(f'time="{time_str}"')

    attrs_str = ' '.join(attrs)
    return f'<msg {attrs_str}>{content}</msg>'


async def _format_reply(reply_msg_id: str) -> str:
    """Look up a replied message and format as XML tag (one level, no recursion)."""
    record = await MessageRecord.filter(message_id=int(reply_msg_id)).first()
    if record is None:
        return f'<reply msg_id="{html.escape(reply_msg_id)}"/>'

    # One-level expansion: plain text from segments, no further reply resolution
    segs = segments_from_json(record.raw_message)
    content = segments_to_plain(segs)
    sender = record.display_name or record.nickname or ""

    return (
        f'<reply msg_id="{html.escape(reply_msg_id)}"'
        f' sender_name="{html.escape(sender)}"'
        f' content="{html.escape(content)}"/>'
    )


async def format_messages_to_xml(messages: list[dict]) -> str:
    """Format multiple messages to LLM-readable XML.

    Each message dict should have: message_id, user_id, nickname, ctx_id, timestamp, raw_message, media_files
    """
    xml_parts: list[str] = []

    for msg in messages:
        ctx_id = msg.get('ctx_id')
        alias = await get_user_alias(msg['user_id'], ctx_id)
        xml = await format_message_to_xml(
            msg_id=msg['message_id'],
            user_id=msg['user_id'],
            nickname=msg.get('nickname'),
            display_name=msg.get('display_name'),
            alias=alias,
            timestamp=msg['timestamp'],
            raw_message=msg['raw_message'],
            media_files=msg.get('media_files', []),
            ctx_id=ctx_id,
        )
        xml_parts.append(xml)

    return '\n'.join(xml_parts)
