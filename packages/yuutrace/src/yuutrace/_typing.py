"""Internal typing helpers for yuutrace."""

from __future__ import annotations

from typing import TypedDict

from .types import LlmCost, LlmUsage


# NOTE: This module intentionally does NOT define a recursive "JSON type system"
# (JsonValue / JsonObject / JsonScalar / JsonArray / JsonSerializable).
#
# Such aliases are type-theater: "JsonObject = dict[str, Any]" collapses to Any
# since Any swallows every type.  The downstream code uses honest types (object,
# dict[str, object], Any) that accurately describe the runtime reality at each
# boundary.  DO NOT re-add a Json* type system — it adds indirection without
# adding safety.
#
# See .agents/skills/python-purist/case-study/type-black-holes.md and
# .agents/skills/python-purist/best-practice/serde-boundary.md for rationale.


SupportsLlmUsage = LlmUsage
SupportsLlmCost = LlmCost


class OtlpAnyValue(TypedDict, total=False):
    stringValue: str
    intValue: str
    doubleValue: float
    boolValue: bool
    bytesValue: str
    arrayValue: OtlpArrayValue
    kvlistValue: OtlpKeyValueList


class OtlpArrayValue(TypedDict):
    values: list[OtlpAnyValue]


class OtlpKeyValue(TypedDict, total=False):
    key: str
    value: OtlpAnyValue


class OtlpKeyValueList(TypedDict):
    values: list[OtlpKeyValue]


class OtlpEvent(TypedDict, total=False):
    name: str
    timeUnixNano: str
    attributes: list[OtlpKeyValue]


class OtlpStatus(TypedDict, total=False):
    code: int
    message: str


class OtlpSpan(TypedDict, total=False):
    traceId: str
    spanId: str
    parentSpanId: str | None
    name: str
    startTimeUnixNano: str
    endTimeUnixNano: str
    status: OtlpStatus
    attributes: list[OtlpKeyValue]
    events: list[OtlpEvent]


class OtlpScopeSpans(TypedDict, total=False):
    spans: list[OtlpSpan]


class OtlpResource(TypedDict, total=False):
    attributes: list[OtlpKeyValue]


class OtlpResourceSpans(TypedDict, total=False):
    resource: OtlpResource
    scopeSpans: list[OtlpScopeSpans]


class EventRecord(TypedDict):
    id: int
    span_id: str
    name: str
    time_unix_nano: int
    attributes: dict[str, object]


class SpanRecord(TypedDict):
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    status_code: int
    status_message: str | None
    conversation_id: str | None
    agent: str | None
    model: str | None
    attributes: dict[str, object]
    resource: dict[str, object]
    events: list[EventRecord]


class ConversationSummary(TypedDict):
    id: str
    agent: str
    model: str | None
    span_count: int
    total_cost: float
    start_time: int
    end_time: int


class ConversationListResult(TypedDict):
    conversations: list[ConversationSummary]
    total: int


class ConversationRecord(TypedDict):
    id: str
    agent: str
    model: str | None
    tags: list[str] | None
    spans: list[SpanRecord]
    total_cost: float
    start_time: int
    end_time: int
