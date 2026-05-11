"""Render gateway messages for actor runtimes."""

from __future__ import annotations

import json

import yuullm

from yuubot.core.messages import IncomingMessage, Segment


def render_incoming_user_message(message: IncomingMessage) -> yuullm.Message:
    return yuullm.user(_render_message_text(message))


def _render_message_text(message: IncomingMessage) -> str:
    metadata = {
        "source": {
            "producer": message.source.producer,
            "id": message.source.id,
            "path": message.source.path,
        },
        "message_id": message.message_id,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "kind": message.kind,
        "segments": [_segment_payload(segment) for segment in message.segments],
    }
    body = _message_text(message)
    if not body:
        body = "(empty message)"
    return "\n".join(
        (
            body,
            "",
            "Message metadata:",
            json.dumps(metadata, ensure_ascii=True, sort_keys=True),
        )
    )


def _message_text(message: IncomingMessage) -> str:
    text_segments = tuple(
        segment.text
        for segment in message.segments
        if segment.kind == "text" and segment.text
    )
    if text_segments:
        return "\n".join(text_segments)
    return message.text


def _segment_payload(segment: Segment) -> dict[str, str]:
    return {
        "kind": segment.kind,
        "text": segment.text,
        "url": segment.url,
        "path": segment.path,
    }
