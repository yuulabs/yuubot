"""In-memory trace store for testing.

Uses :memory: SQLite with the same schema as ``cli/db.py``, so the full
query API (list_conversations, get_conversation, get_span) is available
without any external collector.
"""

from __future__ import annotations

from collections.abc import Sequence
import sqlite3

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.util.types import AttributeValue

from ._typing import (
    ConversationListResult,
    ConversationRecord,
    OtlpAnyValue,
    OtlpKeyValue,
    OtlpResourceSpans,
    OtlpSpan,
    OtlpStatus,
    SpanRecord,
)
from .cli.db import (
    _span_record_from_row,
    get_conversation,
    get_span,
    insert_resource_spans,
    list_conversations,
)


def _attribute_to_otlp_value(value: AttributeValue) -> OtlpAnyValue:
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    values: list[OtlpAnyValue] = []
    for item in value:
        if isinstance(item, str):
            values.append({"stringValue": item})
        elif isinstance(item, bool):
            values.append({"boolValue": item})
        elif isinstance(item, int):
            values.append({"intValue": str(item)})
        elif isinstance(item, float):
            values.append({"doubleValue": item})
    return {"arrayValue": {"values": values}}


def _attribute_to_otlp_pair(key: str, value: AttributeValue) -> OtlpKeyValue:
    return {"key": key, "value": _attribute_to_otlp_value(value)}


def _span_to_otlp_json(span: ReadableSpan) -> OtlpSpan:
    """Convert an SDK ReadableSpan to OTLP-style JSON dict."""
    ctx = span.get_span_context()

    # Attributes
    attrs: list[OtlpKeyValue] = []
    for k, v in (span.attributes or {}).items():
        attrs.append(_attribute_to_otlp_pair(k, v))

    # Events
    events: list[dict[str, str | list[OtlpKeyValue]]] = []
    for ev in span.events or []:
        ev_attrs: list[OtlpKeyValue] = []
        for ek, ev_val in (ev.attributes or {}).items():
            ev_attrs.append(_attribute_to_otlp_pair(ek, ev_val))
        events.append({
            "name": ev.name,
            "timeUnixNano": str(ev.timestamp or 0),
            "attributes": ev_attrs,
        })

    # Status
    status: OtlpStatus = {}
    if span.status is not None:
        status["code"] = span.status.status_code.value
        if span.status.description:
            status["message"] = span.status.description

    parent_id = None
    if span.parent is not None:
        parent_id = format(span.parent.span_id, "016x")

    return {
        "traceId": format(ctx.trace_id, "032x"),
        "spanId": format(ctx.span_id, "016x"),
        "parentSpanId": parent_id,
        "name": span.name,
        "startTimeUnixNano": str(span.start_time or 0),
        "endTimeUnixNano": str(span.end_time or 0),
        "status": status,
        "attributes": attrs,
        "events": events,
    }


class _MemoryExporter(SpanExporter):
    """SpanExporter that writes finished spans into an in-memory SQLite DB."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        resource_spans_list: list[OtlpResourceSpans] = []

        for span in spans:
            # Build resource attributes from span resource
            res_attrs: list[OtlpKeyValue] = []
            if hasattr(span, "resource") and span.resource:
                for k, v in span.resource.attributes.items():
                    res_attrs.append(_attribute_to_otlp_pair(k, v))

            resource_spans_list.append({
                "resource": {"attributes": res_attrs},
                "scopeSpans": [{
                    "spans": [_span_to_otlp_json(span)],
                }],
            })

        insert_resource_spans(self._conn, resource_spans_list)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class MemoryTraceStore:
    """In-memory trace store for testing.

    Wraps a :memory: SQLite database with the yuutrace schema.
    Provides the same query API as ``cli/db.py``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def list_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        agent: str | None = None,
    ) -> ConversationListResult:
        return list_conversations(self.conn, limit=limit, offset=offset, agent=agent)

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        return get_conversation(self.conn, conversation_id)

    def get_span(self, span_id: str) -> SpanRecord | None:
        return get_span(self.conn, span_id)

    def get_all_spans(self) -> list[SpanRecord]:
        rows = self.conn.execute(
            "SELECT * FROM spans ORDER BY start_time_unix_nano"
        ).fetchall()
        return [_span_record_from_row(row) for row in rows]
