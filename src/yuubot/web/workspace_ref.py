"""Conversation API helpers for workspace file references."""

from ..domain.messages import ContentItem


def workspace_ref(path: str) -> str:
    return f"[[ {path.strip()} ]]"


def normalize_conversation_content(content: list[ContentItem]) -> list[ContentItem]:
    parts: list[str] = []
    for item in content:
        if item.kind == "text":
            if item.text:
                parts.append(item.text)
            continue
        if item.path:
            parts.append(workspace_ref(item.path))
    text = "".join(parts).strip()
    return [ContentItem("text", text)] if text else []
