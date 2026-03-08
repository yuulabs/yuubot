"""OneBot V11 message parsing and construction."""

import json

import msgspec

from yuubot.core.models import (
    AtSegment,
    ImageSegment,
    Message,
    MessageEvent,
    MetaEvent,
    NoticeEvent,
    ReplySegment,
    Segment,
    TextSegment,
)


def parse_event(raw: dict) -> MessageEvent | NoticeEvent | MetaEvent | None:
    """Parse raw OneBot V11 JSON into typed event."""
    pt = raw.get("post_type")
    if pt == "message":
        return msgspec.convert(raw, MessageEvent)
    if pt == "notice":
        return msgspec.convert(raw, NoticeEvent)
    if pt == "meta_event":
        return msgspec.convert(raw, MetaEvent)
    return None


def parse_segments(raw_segments: list[dict]) -> Message:
    """Convert OneBot CQ-style segment dicts to internal Segment list."""
    result: list[Segment] = []
    for seg in raw_segments:
        t = seg.get("type", "")
        data = seg.get("data", {})
        if t == "text":
            result.append(TextSegment(text=data.get("text", "")))
        elif t == "image":
            result.append(ImageSegment(
                url=data.get("url", ""),
                file=data.get("file", ""),
                local_path=data.get("local_path", ""),
            ))
        elif t == "at":
            result.append(AtSegment(qq=str(data.get("qq", ""))))
        elif t == "reply":
            result.append(ReplySegment(id=str(data.get("id", ""))))
        else:
            # Unknown segment type — store as text placeholder
            result.append(TextSegment(text=f"[{t}]"))
    return result


def segments_to_onebot(segments: Message) -> list[dict]:
    """Convert internal segments to OneBot V11 message array."""
    result: list[dict] = []
    for seg in segments:
        if isinstance(seg, TextSegment):
            result.append({"type": "text", "data": {"text": seg.text}})
        elif isinstance(seg, ImageSegment):
            d: dict = {}
            if seg.url:
                d["url"] = seg.url
            if seg.file:
                d["file"] = seg.file
            result.append({"type": "image", "data": d})
        elif isinstance(seg, AtSegment):
            result.append({"type": "at", "data": {"qq": seg.qq}})
        elif isinstance(seg, ReplySegment):
            result.append({"type": "reply", "data": {"id": seg.id}})
    return result


def build_send_msg(
    msg_type: str,
    target_id: int,
    segments: Message,
) -> dict:
    """Build a send_msg API request body for OneBot V11."""
    body: dict = {
        "message_type": msg_type,
        "message": segments_to_onebot(segments),
    }
    if msg_type == "group":
        body["group_id"] = target_id
    else:
        body["user_id"] = target_id
    return body
