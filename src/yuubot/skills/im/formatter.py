"""Format messages to LLM-readable XML.

Example:
  <msg id="42" name="Alice" qq="123456789" time="03-10 19:11 +0800"><quote from="Bob">原文...</quote>回复内容</msg>
"""

import html
import os
from datetime import datetime, timezone

from yuubot.core.models import (
    ImageSegment,
    Message,
    MessageRecord,
    ReplySegment,
    segments_from_json,
    UserAlias,
)

# Cache for user aliases to avoid N+1 queries
_alias_cache: dict[tuple[int, str], str | None] = {}


def clear_alias_cache() -> None:
    """Clear the alias cache. Call this when aliases are updated."""
    _alias_cache.clear()


def _docker_host_path(path: str) -> str:
    """Prefix a host path with the Docker mount point when running inside Docker."""
    mount = os.environ.get("YUU_DOCKER_HOST_MOUNT", "")
    if mount and not path.startswith(mount):
        return f"{mount}{path}"
    return path


async def get_user_alias(user_id: int, ctx_id: int | None = None) -> str | None:
    """Get user alias for a given user_id and context."""
    # Check context-specific alias first
    if ctx_id is not None:
        scope = f"ctx_{ctx_id}"
        cache_key = (user_id, scope)
        if cache_key in _alias_cache:
            return _alias_cache[cache_key]

        alias = await UserAlias.filter(user_id=user_id, scope=scope).first()
        if alias:
            _alias_cache[cache_key] = alias.alias
            return alias.alias
        _alias_cache[cache_key] = None

    # Check global alias
    global_scope = "global"
    cache_key = (user_id, global_scope)
    if cache_key in _alias_cache:
        return _alias_cache[cache_key]

    alias = await UserAlias.filter(user_id=user_id, scope=global_scope).first()
    result = alias.alias if alias else None
    _alias_cache[cache_key] = result
    return result


def _best_name(nickname: str | None, display_name: str | None, alias: str | None) -> str:
    """Pick the best display name: alias > display_name > nickname > empty."""
    return alias or display_name or nickname or ""


async def _resolve_at_name(qq: str, ctx_id: int | None = None) -> str:
    """Resolve an @mention QQ number to a display name."""
    try:
        uid = int(qq)
    except ValueError:
        return qq

    alias = await get_user_alias(uid, ctx_id)
    if alias:
        return alias

    record = await MessageRecord.filter(user_id=uid).order_by("-id").first()
    if record:
        return _best_name(record.nickname, record.display_name, None)

    return qq


async def format_segments(
    segments: Message,
    media_files: list[str] | None = None,
    ctx_id: int | None = None,
    *,
    extract_replies: bool = False,
) -> str | tuple[str, list[str]]:
    """Format segments to LLM-readable inline content.

    If extract_replies=True, returns (content, reply_tags) where reply_tags
    are standalone <reply> elements to be placed before <msg>.
    Otherwise returns content string with replies inline (legacy behavior).
    """
    if media_files is None:
        media_files = []

    parts: list[str] = []
    reply_tags: list[str] = []
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
            if extract_replies:
                tag = await _format_reply_standalone(seg.id, ctx_id)
                reply_tags.append(tag)
            else:
                tag = await _format_reply_inline(seg.id, ctx_id)
                parts.append(tag)

    content = ''.join(parts)
    if extract_replies:
        return content, reply_tags
    return content


def _format_time(ts: datetime) -> str:
    """Format timestamp in local timezone: MM-DD HH:MM +HHMM."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone().strftime("%m-%d %H:%M %z")


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
    """Format a single message to LLM-readable XML."""
    segments = segments_from_json(raw_message)
    content, reply_tags = await format_segments(
        segments, media_files, ctx_id=ctx_id, extract_replies=True,
    )

    name = _best_name(nickname, display_name, alias)

    # Build attributes
    attrs_parts = [f'id="{msg_id}"']
    if name:
        attrs_parts.append(f'name="{html.escape(name)}"')
    attrs_parts.append(f'qq="{user_id}"')

    if isinstance(timestamp, datetime):
        ts = timestamp
    else:
        ts = datetime.fromisoformat(str(timestamp))
    attrs_parts.append(f'time="{_format_time(ts)}"')

    attrs_str = ' '.join(attrs_parts)
    quote_str = ''.join(reply_tags)
    return f'<msg {attrs_str}>{quote_str}{content}</msg>'


async def _format_reply_standalone(reply_msg_id: str, ctx_id: int | None = None) -> str:
    """Format a quoted message as <quote from="sender">content</quote>."""
    record = await MessageRecord.filter(message_id=int(reply_msg_id)).first()
    if record is None:
        return f'<reply to="?">[unknown message]</reply>'

    segs = segments_from_json(record.raw_message)
    content = await format_segments(segs, record.media_files)
    alias = await get_user_alias(record.user_id, ctx_id)
    sender = _best_name(record.nickname, record.display_name, alias)

    return f'<quote from="{html.escape(sender)}">{content}</quote>'


async def _format_reply_inline(reply_msg_id: str, ctx_id: int | None = None) -> str:
    """Format a reply inline (legacy format for non-XML contexts)."""
    record = await MessageRecord.filter(message_id=int(reply_msg_id)).first()
    if record is None:
        return f'<reply msg_id="{html.escape(reply_msg_id)}"/>'

    segs = segments_from_json(record.raw_message)
    content = await format_segments(segs, record.media_files)
    alias = await get_user_alias(record.user_id, ctx_id)
    sender = _best_name(record.nickname, record.display_name, alias)

    return (
        f'<reply msg_id="{html.escape(reply_msg_id)}"'
        f' sender_name="{html.escape(sender)}"'
        f' content="{html.escape(content)}"/>'
    )


async def format_messages_to_xml(messages: list[dict]) -> str:
    """Format multiple messages to LLM-readable XML."""
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
