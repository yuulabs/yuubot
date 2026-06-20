"""Shared helpers for conversation content decoding."""

from __future__ import annotations

import msgspec
from yuuagents import RuntimeEvent

def _decode_content(raw_content: str) -> list[dict[str, object]]:
    return msgspec.json.decode(raw_content.encode())


def _content_to_builtins(content: object) -> list[dict[str, object]]:
    value = msgspec.to_builtins(content)
    if not isinstance(value, list):
        return [{"type": "text", "text": str(value)}]
    result: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(_json_safe_dict(item))
        else:
            result.append({"type": "text", "text": str(item)})
    return result


def _event_metadata(event: RuntimeEvent) -> dict[str, object]:
    from yuubot.core.conversations import LLMFinishedData

    llm = LLMFinishedData.from_event(event)
    return _json_safe_dict(
        {
            "model": llm.model or None,
            "usage": llm.usage,
            "cost": llm.cost,
            "duration_s": llm.duration_s,
            "tool_calls": list(llm.tool_calls) if llm.tool_calls else None,
        }
    )


# ---------------------------------------------------------------------------
# JSON-safe value coercion
# ---------------------------------------------------------------------------


def _json_safe_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_safe(raw) for key, raw in value.items() if raw is not None}


def _json_safe(value: object) -> object:
    try:
        return msgspec.to_builtins(value)
    except TypeError:
        return repr(value)
