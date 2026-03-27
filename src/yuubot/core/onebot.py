"""OneBot V11 message parsing and construction."""


import msgspec

from yuubot.core.models import (
    AtSegment,
    ImageSegment,
    JsonSegment,
    Message,
    MessageEvent,
    MetaEvent,
    NoticeEvent,
    PokeSegment,
    ReplySegment,
    ForwardSegment,
    Segment,
    TextSegment,
)
from yuubot.core.types import InboundMessage, Sender


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
        elif t in {"forward", "node"} and data.get("id"):
            result.append(ForwardSegment(
                id=str(data.get("id", "")),
                summary=str(data.get("summary", "")),
            ))
        elif t == "json":
            result.append(JsonSegment(data=data.get("data", "")))
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
        elif isinstance(seg, ForwardSegment):
            result.append({"type": "forward", "data": {"id": seg.id, "summary": seg.summary}})
        elif isinstance(seg, JsonSegment):
            result.append({"type": "json", "data": {"data": seg.data}})
        elif isinstance(seg, PokeSegment):
            # Poke is sent via /group_poke API, not as a message segment.
            # This is just for completeness in serialization.
            result.append({"type": "poke", "data": {
                "sender_qq": seg.sender_qq, "target_qq": seg.target_qq,
                "action": seg.action, "suffix": seg.suffix,
            }})
    return result


def to_inbound_message(event: dict) -> InboundMessage:
    """Convert a raw OneBot V11 message event dict to an InboundMessage."""
    sender_dict = event.get("sender", {})
    sender = Sender(
        user_id=event.get("user_id", 0),
        nickname=sender_dict.get("nickname", ""),
        card=sender_dict.get("card", ""),
        role=sender_dict.get("role", ""),
    )
    msg_type = event.get("message_type", "private")
    ctx_id = event.get("ctx_id", 0)
    segments = parse_segments(event.get("message", []))
    return InboundMessage(
        message_id=event.get("message_id", 0),
        ctx_id=ctx_id,
        chat_type=msg_type,
        group_id=event.get("group_id", 0),
        self_id=event.get("self_id", 0),
        sender=sender,
        segments=segments,
        timestamp=event.get("time", 0),
        raw_message=event.get("raw_message", ""),
        extra_messages=[to_inbound_message(extra) for extra in event.get("_extra_events", [])],
        raw_event=event,
    )


def parse_poke_notice(raw: dict) -> PokeSegment | None:
    """Extract a PokeSegment from a poke notice event.

    NapCat poke events have: user_id (sender), target_id (target),
    and action text in raw_message array or action/suffix fields.
    """
    if raw.get("sub_type") != "poke":
        return None
    sender = str(raw.get("user_id", raw.get("operator_id", "")))
    target = str(raw.get("target_id", ""))
    if not sender or not target:
        return None

    # Try go-cqhttp style fields first
    action = raw.get("action", "")
    suffix = raw.get("suffix", "")

    # Fallback: parse raw_message array (NapCat style)
    if not action:
        raw_msg = raw.get("raw_message", raw.get("raw_info", []))
        if isinstance(raw_msg, list):
            for item in raw_msg:
                if isinstance(item, dict) and item.get("type") == "nor":
                    text = item.get("txt", "")
                    if text:
                        action = text
                        break
    if not action:
        action = "戳了戳"

    return PokeSegment(sender_qq=sender, target_qq=target, action=action, suffix=suffix)


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
