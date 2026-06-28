"""Typed views over yuuagents runtime event payloads."""

from __future__ import annotations

from dataclasses import dataclass

import msgspec
from yuuagents.core.eventbus import RuntimeEvent


@dataclass(frozen=True)
class AgentEventIdentity:
    """Typed extraction of identity fields from RuntimeEvent.data."""

    agent_id: str
    entity_id: str = ""
    parent_id: str = ""

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> AgentEventIdentity:
        data = event.data
        return cls(
            agent_id=event.agent_id or "",
            entity_id=str(data.get("entity_id") or ""),
            parent_id=str(data.get("parent_id") or ""),
        )


@dataclass(frozen=True)
class EntityData:
    """Typed extraction of entity fields from RuntimeEvent.data."""

    entity_id: str = ""
    entity_type: str = ""
    parent_id: str = ""
    tool_call_id: str = ""
    status: str = ""

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> EntityData:
        data = event.data
        return cls(
            entity_id=str(data.get("entity_id") or ""),
            entity_type=str(data.get("entity_type") or ""),
            parent_id=str(data.get("parent_id") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            status=str(data.get("status") or ""),
        )


@dataclass(frozen=True)
class ChunkData:
    """Typed extraction of chunk fields from RuntimeEvent.data."""

    entity_id: str = ""
    entity_type: str = ""
    parent_id: str = ""
    tool_call_id: str = ""
    chunk_index: int = 0
    blocks: tuple[object, ...] = ()

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> ChunkData:
        data = event.data
        raw_blocks = data.get("blocks", [])
        blocks = tuple(raw_blocks) if isinstance(raw_blocks, list) else ()
        return cls(
            entity_id=str(data.get("entity_id") or ""),
            entity_type=str(data.get("entity_type") or ""),
            parent_id=str(data.get("parent_id") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            chunk_index=_int_value(data.get("chunk_index")),
            blocks=blocks,
        )


@dataclass(frozen=True)
class LLMFinishedData:
    """Typed extraction of llm.finished fields from RuntimeEvent.data."""

    model: str = ""
    usage: dict[str, object] | None = None
    cost: dict[str, object] | float | None = None
    duration_s: float | None = None
    tool_calls: tuple[dict[str, object], ...] = ()
    message: object | None = None

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> LLMFinishedData:
        data = event.data
        raw_calls = data.get("tool_calls", [])
        tool_calls = _tool_calls(raw_calls)
        return cls(
            model=str(data.get("model") or ""),
            usage=_dict_value(data.get("usage")),
            cost=_cost_value(data.get("cost")),
            duration_s=_float_value(data.get("duration_s")),
            tool_calls=tool_calls,
            message=data.get("message"),
        )


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float_value(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _dict_value(value: object) -> dict[str, object] | None:
    raw = msgspec.to_builtins(value)
    if not isinstance(raw, dict):
        return None
    return {str(key): item for key, item in raw.items()}


def _cost_value(value: object) -> dict[str, object] | float | None:
    if isinstance(value, int | float):
        return float(value)
    return _dict_value(value)


def _cost_total(value: dict[str, object] | float | None) -> float | None:
    """Extract the ``total_cost`` USD figure from an ``llm.finished`` cost payload.

    The runtime emits ``cost`` as a ``yuullm.Cost`` msgspec.Struct; by the
    time it reaches ``LLMFinishedData`` it has been normalised to a dict
    (``{"total_cost": float, ...}``). A bare ``float`` (legacy / scalar
    cost) is returned as-is. ``None`` (no usage / no pricing) → ``None``.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    total = value.get("total_cost")
    if isinstance(total, int | float):
        return float(total)
    return None

def _tool_calls(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    result: list[dict[str, object]] = []
    for item in value:
        data = _dict_value(item)
        if data is not None:
            result.append(data)
    return tuple(result)

