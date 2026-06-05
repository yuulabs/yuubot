"""Render chat dialog messages as human-readable text lines.

Provides pure functions for extracting text from multimodal ContentItem dicts
and formatting them into pre-rendered dialog lines for FTS indexing and
agent-facing history retrieval.
"""

from __future__ import annotations

from datetime import datetime, timezone


def render_message_text(content_items: list[dict[str, object]]) -> str:
    """Extract human-readable text from ContentItem dicts.

    text items   → include the text directly
    image items  → [image: url_or_name]
    audio items  → [audio: url_or_name]
    file items   → [file: file_name]
    unknown type → [type_name] fallback
    """
    parts: list[str] = []
    for item in content_items:
        item_type = item.get("type", "")
        if item_type == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif item_type == "image":
            url = item.get("image_url", item.get("file_name", "embedded"))
            parts.append(f"[image: {url}]")
        elif item_type == "audio":
            url = item.get("audio_url", item.get("file_name", "embedded"))
            parts.append(f"[audio: {url}]")
        elif item_type == "file":
            name = item.get("file_name", "attachment")
            parts.append(f"[file: {name}]")
        else:
            parts.append(f"[{item_type}]")
    return " ".join(parts)


def render_dialog_line(
    message_id: str,
    sender_name: str,
    timestamp: int,
    text: str,
) -> str:
    """Render a single message line in dialog-chat format.

    Produces: [{message_id} {sender_name} {YYYY-MM-DD HH:MM:SS}] {text}
    The timestamp is a Unix epoch (int) converted to UTC.
    """
    ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return f"[{message_id} {sender_name} {ts:%Y-%m-%d %H:%M:%S}] {text}"
