"""Format messages to LLM-readable XML.

Reply is extracted as a sibling element before <msg>, and msg attributes
are simplified: best name only, shorter time format, no redundant fields.

Before:
  <msg id="..." qq="..." name="x" display_name="y" alias="z" time="2026-03-10 11:11 +0000">
    <reply msg_id="..." sender_name="bot" content="不是哦..."/> 为什么喜欢百合</msg>

After:
  <reply to="夕雨yuu">不是哦...</reply>
  <msg name="繁星入梦" qq="948523603" time="03-10 19:11 +0800">为什么喜欢百合</msg>
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
    if ctx_id is not None:
        alias = await UserAlias.filter(user_id=user_id, scope=f"ctx_{ctx_id}").first()
        if alias:
            return alias.alias

    alias = await UserAlias.filter(user_id=user_id, scope="global").first()
    return alias.alias if alias else None


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
                tag = await _format_reply_standalone(seg.id)
                reply_tags.append(tag)
            else:
                tag = await _format_reply_inline(seg.id)
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
    """Format a single message to LLM-readable XML.

    New format:
      <reply to="sender">content</reply>
      <msg name="best_name" qq="123" time="03-10 11:11">content</msg>
    """
    segments = segments_from_json(raw_message)
    content, reply_tags = await format_segments(
        segments, media_files, ctx_id=ctx_id, extract_replies=True,
    )

    name = _best_name(nickname, display_name, alias)

    # Build attributes
    attrs_parts = []
    if name:
        attrs_parts.append(f'name="{html.escape(name)}"')
    attrs_parts.append(f'qq="{user_id}"')

    if isinstance(timestamp, datetime):
        ts = timestamp
    else:
        ts = datetime.fromisoformat(str(timestamp))
    attrs_parts.append(f'time="{_format_time(ts)}"')

    attrs_str = ' '.join(attrs_parts)
    msg_tag = f'<msg {attrs_str}>{content}</msg>'

    if reply_tags:
        return '\n'.join(reply_tags) + '\n' + msg_tag
    return msg_tag


async def _format_reply_standalone(reply_msg_id: str) -> str:
    """Format a reply as a standalone sibling element: <reply to="sender">content</reply>."""
    record = await MessageRecord.filter(message_id=int(reply_msg_id)).first()
    if record is None:
        return f'<reply to="?">[unknown message]</reply>'

    segs = segments_from_json(record.raw_message)
    content = segments_to_plain(segs)
    sender = _best_name(record.nickname, record.display_name, None)

    return f'<reply to="{html.escape(sender)}">{html.escape(content)}</reply>'


async def _format_reply_inline(reply_msg_id: str) -> str:
    """Format a reply inline (legacy format for non-XML contexts)."""
    record = await MessageRecord.filter(message_id=int(reply_msg_id)).first()
    if record is None:
        return f'<reply msg_id="{html.escape(reply_msg_id)}"/>'

    segs = segments_from_json(record.raw_message)
    content = segments_to_plain(segs)
    sender = record.display_name or record.nickname or ""

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
