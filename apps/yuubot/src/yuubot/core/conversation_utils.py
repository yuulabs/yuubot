"""Shared helpers for conversation event processing and content decoding."""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec
from yuuagents import RuntimeEvent

if TYPE_CHECKING:
    from yuubot.core.conversations import AgentEvent


# ---------------------------------------------------------------------------
# Agent event helpers
# ---------------------------------------------------------------------------


def _agent_event(
    conversation_id: str,
    event: RuntimeEvent,
    event_type: str,
    content: dict[str, object],
) -> "AgentEvent":
    from yuubot.core.conversations import AgentEvent as _AgentEvent

    return _AgentEvent(
        conversation_id=conversation_id,
        agent_id=_agent_id_for_event(event),
        agent_name=event.agent_name,
        event_type=event_type,
        content=content,
        timestamp=event.timestamp,
    )


def _agent_id_for_event(event: RuntimeEvent) -> str:
    from yuubot.core.conversations import AgentEventIdentity

    identity = AgentEventIdentity.from_event(event)
    if identity.agent_id:
        return identity.agent_id
    if identity.parent_id:
        return identity.parent_id
    return identity.entity_id


# ---------------------------------------------------------------------------
# Entity / chunk / LLM content extraction
# ---------------------------------------------------------------------------


def _entity_content(event: RuntimeEvent) -> dict[str, object]:
    from yuubot.core.conversations import EntityData

    entity = EntityData.from_event(event)
    return _json_safe_dict(
        {
            "entity_id": entity.entity_id or None,
            "entity_type": entity.entity_type or None,
            "parent_id": entity.parent_id or None,
            "tool_call_id": entity.tool_call_id or None,
            "status": entity.status or None,
        }
    )


def _chunk_event_type(event: RuntimeEvent) -> str:
    if _is_tool_entity(event):
        return "tool_result"
    kinds = {_block_content_kind(block) for block in _event_blocks(event)}
    if "tool_call" in kinds:
        return "tool_call"
    if "thinking" in kinds:
        return "thinking"
    if "text" in kinds:
        return "text"
    return "output"


def _entity_end_event_type(event: RuntimeEvent) -> str:
    return "tool_result" if _is_tool_entity(event) else "entity_end"


def _chunk_content(event: RuntimeEvent) -> dict[str, object]:
    from yuubot.core.conversations import ChunkData

    chunk = ChunkData.from_event(event)
    result: dict[str, object] = {}
    if chunk.entity_id:
        result["entity_id"] = chunk.entity_id
    if chunk.entity_type:
        result["entity_type"] = chunk.entity_type
    if chunk.parent_id:
        result["parent_id"] = chunk.parent_id
    if chunk.tool_call_id:
        result["tool_call_id"] = chunk.tool_call_id
    result["chunk_index"] = chunk.chunk_index
    if chunk.blocks:
        result["blocks"] = _json_safe(list(chunk.blocks))
    return result


def _event_blocks(event: RuntimeEvent) -> list[object]:
    from yuubot.core.conversations import ChunkData

    chunk = ChunkData.from_event(event)
    return list(chunk.blocks)


def _is_tool_entity(event: RuntimeEvent) -> bool:
    from yuubot.core.conversations import EntityData

    entity = EntityData.from_event(event)
    return bool(entity.parent_id)


def _block_content_kind(block: object) -> str:
    raw = msgspec.to_builtins(block)
    if not isinstance(raw, dict):
        return "text"
    content = raw.get("content")
    if isinstance(content, str):
        return "text"
    if isinstance(content, dict):
        kind = content.get("type")
        if isinstance(kind, str):
            if "thinking" in kind:
                return "thinking"
            if kind == "tool_call":
                return "tool_call"
            if kind == "text":
                return "text"
            return kind
    return "output"


# ---------------------------------------------------------------------------
# Content decoding / encoding
# ---------------------------------------------------------------------------


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
