"""SQLite persistence layer for yuutrace.

Stores OTLP trace data in a local SQLite database and provides
query functions for the REST API.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from typing import cast

from .._typing import (
    ConversationListResult,
    ConversationRecord,
    ConversationSummary,
    EventRecord,
    OtlpAnyValue,
    OtlpKeyValue,
    OtlpResource,
    OtlpResourceSpans,
    SpanRecord,
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS spans (
    trace_id             TEXT NOT NULL,
    span_id              TEXT NOT NULL PRIMARY KEY,
    parent_span_id       TEXT,
    name                 TEXT NOT NULL,
    start_time_unix_nano INTEGER NOT NULL,
    end_time_unix_nano   INTEGER NOT NULL,
    status_code          INTEGER NOT NULL DEFAULT 0,
    status_message       TEXT,
    attributes_json      TEXT NOT NULL DEFAULT '{}',
    conversation_id      TEXT,
    agent                TEXT,
    model                TEXT,
    resource_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_spans_conversation_id
    ON spans(conversation_id);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id
    ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_start_time
    ON spans(start_time_unix_nano);

CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    span_id           TEXT NOT NULL REFERENCES spans(span_id),
    name              TEXT NOT NULL,
    time_unix_nano    INTEGER NOT NULL,
    attributes_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_span_id
    ON events(span_id);
CREATE INDEX IF NOT EXISTS idx_events_name
    ON events(name);

CREATE TABLE IF NOT EXISTS blobs (
    sha256      TEXT NOT NULL PRIMARY KEY,
    media_type  TEXT NOT NULL,
    data        BLOB NOT NULL
);
"""

type QueryParam = str | int


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the database and ensure the schema exists."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# OTLP JSON helpers
#
# NOTE: This layer uses dict[str, object] and object for parsed JSON data.
# The data is genuinely untyped at this boundary — it arrives as raw JSON
# from the database.  dict[str, object] is honest about what we have.
# DO NOT re-introduce fake "JsonObject"/"JsonValue" type aliases here.
# ---------------------------------------------------------------------------


def _as_int(value: str | int | float | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: str | int | float | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_str(value: object | None) -> str | None:
    return value if isinstance(value, str) else None


def _string_list(value: object | None) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return cast(list[str], value)
    return None


def _json_object_from_string(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return cast(dict[str, object], parsed)
    return {}


def _otlp_attr_value(attr: OtlpAnyValue) -> object:
    """Extract the typed value from an OTLP attribute entry.

    OTLP JSON encodes attribute values as ``{"stringValue": "..."}`` etc.
    """
    if "stringValue" in attr:
        return attr["stringValue"]
    if "intValue" in attr:
        return int(attr["intValue"])
    if "doubleValue" in attr:
        return float(attr["doubleValue"])
    if "boolValue" in attr:
        return attr["boolValue"]
    if "arrayValue" in attr:
        values = attr["arrayValue"].get("values", [])
        return [_otlp_attr_value(v) for v in values]
    if "bytesValue" in attr:
        return attr["bytesValue"]
    if "kvlistValue" in attr:
        pairs = attr["kvlistValue"].get("values", [])
        kv_dict: dict[str, object] = {}
        for pair in pairs:
            key = pair.get("key")
            if isinstance(key, str):
                kv_dict[key] = _otlp_attr_value(pair.get("value", {}))
        return kv_dict
    # Fallback: return the raw dict
    return cast(dict[str, object], dict(attr))


def _parse_attributes(attr_list: list[OtlpKeyValue]) -> dict[str, object]:
    """Convert an OTLP ``attributes`` array to a flat dict."""
    attrs: dict[str, object] = {}
    for attr in attr_list:
        key = attr.get("key")
        if isinstance(key, str):
            attrs[key] = _otlp_attr_value(attr.get("value", {}))
    return attrs


def _parse_resource_attributes(resource: OtlpResource) -> dict[str, object]:
    """Extract resource-level attributes."""
    return _parse_attributes(resource.get("attributes", []))


# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------


def _extract_blobs(conn: sqlite3.Connection, attrs: dict[str, object]) -> dict[str, object]:
    """Walk attrs, extract base64 image data into blobs table, replace with [blob:<sha256>]."""

    def _write_blob(b64_str: str, media_type: str) -> str:
        raw = base64.b64decode(b64_str)
        sha = hashlib.sha256(raw).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO blobs (sha256, media_type, data) VALUES (?, ?, ?)",
            (sha, media_type, raw),
        )
        return f"[blob:{sha}]"

    def _walk(obj: object) -> object:
        if isinstance(obj, list):
            return [_walk(item) for item in obj]
        if not isinstance(obj, dict):
            return obj
        # Anthropic format: {"type": "base64", "media_type": "...", "data": "..."}
        data = obj.get("data")
        if obj.get("type") == "base64" and isinstance(data, str) and not data.startswith("["):
            media_type = obj.get("media_type")
            ref = _write_blob(data, media_type if isinstance(media_type, str) else "image/png")
            return {**obj, "data": ref}
        # OpenAI format: {"url": "data:<mime>;base64,..."}
        url_value = obj.get("url")
        if isinstance(url_value, str) and url_value.startswith("data:"):
            url = url_value
            try:
                header, b64 = url.split(",", 1)
                mime = header.split(":", 1)[1].split(";", 1)[0]
            except Exception:
                mime, b64 = "image/png", url
            ref = _write_blob(b64, mime)
            return {**obj, "url": ref}
        return {k: _walk(v) for k, v in obj.items()}

    return cast(dict[str, object], _walk(attrs))


def get_blob(conn: sqlite3.Connection, sha256: str) -> tuple[str, bytes] | None:
    """Return (media_type, data) for a stored blob, or None if not found."""
    row = conn.execute(
        "SELECT media_type, data FROM blobs WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if row is None:
        return None
    return row["media_type"], bytes(row["data"])


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def insert_resource_spans(conn: sqlite3.Connection, resource_spans: list[OtlpResourceSpans]) -> int:
    """Parse OTLP JSON ``resourceSpans`` and insert into the database.

    Returns the number of spans inserted.
    """
    count = 0
    for rs in resource_spans:
        resource_attrs = _parse_resource_attributes(cast(OtlpResource, rs.get("resource", {})))
        resource_json = json.dumps(resource_attrs, ensure_ascii=False)

        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                attrs = _parse_attributes(span.get("attributes", []))
                attrs = _extract_blobs(conn, attrs)
                attrs_json = json.dumps(attrs, ensure_ascii=False)

                # Denormalized fields from attributes
                conversation_id = attrs.get("yuu.conversation.id")
                agent = attrs.get("yuu.agent")
                model = attrs.get("yuu.conversation.model")

                # Status
                status = span.get("status", {})
                status_code = _as_int(status.get("code", 0))
                status_message = _optional_str(status.get("message"))

                span_id = span.get("spanId")
                if not isinstance(span_id, str):
                    continue

                conn.execute(
                    """INSERT OR REPLACE INTO spans
                       (trace_id, span_id, parent_span_id, name,
                        start_time_unix_nano, end_time_unix_nano,
                        status_code, status_message, attributes_json,
                        conversation_id, agent, model, resource_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        span.get("traceId", ""),
                        span_id,
                        span.get("parentSpanId") if isinstance(span.get("parentSpanId"), str) else None,
                        span.get("name", ""),
                        _as_int(span.get("startTimeUnixNano", 0)),
                        _as_int(span.get("endTimeUnixNano", 0)),
                        status_code,
                        status_message,
                        attrs_json,
                        _optional_str(conversation_id),
                        _optional_str(agent),
                        _optional_str(model),
                        resource_json,
                    ),
                )

                # Events
                for event in span.get("events", []):
                    event_attrs = _parse_attributes(event.get("attributes", []))
                    # Extract blobs from turn event items (images, etc.)
                    event_name = event.get("name", "")
                    if event_name == "yuu.turn":
                        items_json = event_attrs.get("yuu.turn.items")
                        if isinstance(items_json, str):
                            try:
                                items = json.loads(items_json)
                                cleaned = _extract_blobs(conn, {"items": items})
                                event_attrs["yuu.turn.items"] = json.dumps(
                                    cleaned["items"], ensure_ascii=False
                                )
                            except (json.JSONDecodeError, KeyError):
                                pass
                    conn.execute(
                        """INSERT INTO events
                           (span_id, name, time_unix_nano, attributes_json)
                           VALUES (?, ?, ?, ?)""",
                        (
                            span_id,
                            event_name,
                            _as_int(event.get("timeUnixNano", 0)),
                            json.dumps(event_attrs, ensure_ascii=False),
                        ),
                    )

                count += 1

    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _row_str(row: sqlite3.Row, key: str, default: str = "") -> str:
    value = row[key]
    return value if isinstance(value, str) else default


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    return value if isinstance(value, str) else None


def _row_int(row: sqlite3.Row, key: str, default: int = 0) -> int:
    return _as_int(row[key], default)


def _row_float(row: sqlite3.Row, key: str, default: float = 0.0) -> float:
    return _as_float(row[key], default)


def _event_record_from_row(row: sqlite3.Row) -> EventRecord:
    return {
        "id": _row_int(row, "id"),
        "span_id": _row_str(row, "span_id"),
        "name": _row_str(row, "name"),
        "time_unix_nano": _row_int(row, "time_unix_nano"),
        "attributes": _json_object_from_string(_row_optional_str(row, "attributes_json")),
    }


def _span_record_from_row(row: sqlite3.Row) -> SpanRecord:
    return {
        "trace_id": _row_str(row, "trace_id"),
        "span_id": _row_str(row, "span_id"),
        "parent_span_id": _row_optional_str(row, "parent_span_id"),
        "name": _row_str(row, "name"),
        "start_time_unix_nano": _row_int(row, "start_time_unix_nano"),
        "end_time_unix_nano": _row_int(row, "end_time_unix_nano"),
        "status_code": _row_int(row, "status_code"),
        "status_message": _row_optional_str(row, "status_message"),
        "conversation_id": _row_optional_str(row, "conversation_id"),
        "agent": _row_optional_str(row, "agent"),
        "model": _row_optional_str(row, "model"),
        "attributes": _json_object_from_string(_row_optional_str(row, "attributes_json")),
        "resource": _json_object_from_string(_row_optional_str(row, "resource_json")),
        "events": [],
    }


def _attach_events(conn: sqlite3.Connection, spans: list[SpanRecord]) -> None:
    """Attach events to each span dict in-place."""
    if not spans:
        return
    span_ids = [s["span_id"] for s in spans]
    placeholders = ",".join("?" * len(span_ids))
    rows = conn.execute(
        f"SELECT * FROM events WHERE span_id IN ({placeholders}) ORDER BY time_unix_nano",
        span_ids,
    ).fetchall()

    events_by_span: dict[str, list[EventRecord]] = {}
    for row in rows:
        event = _event_record_from_row(row)
        events_by_span.setdefault(event["span_id"], []).append(event)

    for span in spans:
        span["events"] = events_by_span.get(span["span_id"], [])


def list_conversations(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    agent: str | None = None,
) -> ConversationListResult:
    """Return a paginated list of conversations with summary stats.

    Returns ``{"conversations": [...], "total": int}``.
    """
    where = "WHERE conversation_id IS NOT NULL"
    params: list[QueryParam] = []
    if agent:
        where += " AND agent = ?"
        params.append(agent)

    # Total count
    total_row = conn.execute(
        f"SELECT COUNT(DISTINCT conversation_id) FROM spans {where}",
        params,
    ).fetchone()
    total = _as_int(total_row[0] if total_row is not None else None)

    # All child spans (llm_gen, tool:*) also carry conversation_id, so we can
    # aggregate directly by conversation_id across multiple trace_ids (continuations).
    rows = conn.execute(
        f"""SELECT
                conversation_id AS id,
                MAX(agent) AS agent,
                MAX(model) AS model,
                COUNT(DISTINCT span_id) AS span_count,
                MIN(start_time_unix_nano) AS start_time,
                MAX(end_time_unix_nano) AS end_time
            FROM spans
            {where}
            GROUP BY conversation_id
            ORDER BY start_time DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    conversations: list[ConversationSummary] = []
    for row in rows:
        cid = _row_str(row, "id")
        # Compute total cost across all traces sharing this conversation_id
        cost_row = conn.execute(
            """SELECT COALESCE(SUM(
                   json_extract(e.attributes_json, '$."yuu.cost.amount"')
               ), 0) AS total_cost
               FROM events e
               JOIN spans s ON e.span_id = s.span_id
               WHERE s.conversation_id = ? AND e.name = 'yuu.cost'""",
            (cid,),
        ).fetchone()
        conversations.append(
            {
                "id": cid,
                "agent": _row_str(row, "agent"),
                "model": _row_optional_str(row, "model"),
                "span_count": _row_int(row, "span_count"),
                "start_time": _row_int(row, "start_time"),
                "end_time": _row_int(row, "end_time"),
                "total_cost": _as_float(cost_row[0] if cost_row is not None else None),
            }
        )

    return {"conversations": conversations, "total": total}


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> ConversationRecord | None:
    """Return all spans and events for a single conversation.

    Fetches ALL spans carrying this conversation_id across all trace_ids.
    This correctly handles multi-turn conversations where each continuation
    produces a new OTEL trace but shares the same conversation_id attribute.
    """
    rows = conn.execute(
        """SELECT * FROM spans
           WHERE conversation_id = ?
           ORDER BY start_time_unix_nano""",
        (conversation_id,),
    ).fetchall()

    if not rows:
        return None

    spans = [_span_record_from_row(row) for row in rows]
    _attach_events(conn, spans)

    # Aggregate metadata from the first span that has them
    agent: str = cast(str, next((s.get("agent") for s in spans if s.get("agent")), ""))
    model = next((s.get("model") for s in spans if s.get("model")), None)
    tags = next(
        (
            parsed
            for span in spans
            if (parsed := _string_list(span["attributes"].get("yuu.conversation.tags"))) is not None
        ),
        None,
    )

    # Total cost across all traces sharing this conversation_id
    cost_row = conn.execute(
        """SELECT COALESCE(SUM(
               json_extract(e.attributes_json, '$."yuu.cost.amount"')
           ), 0)
           FROM events e
           JOIN spans s ON e.span_id = s.span_id
           WHERE s.conversation_id = ? AND e.name = 'yuu.cost'""",
        (conversation_id,),
    ).fetchone()

    result: ConversationRecord = {
        "id": conversation_id,
        "agent": agent,
        "model": model,
        "tags": tags,
        "spans": spans,
        "total_cost": _as_float(cost_row[0] if cost_row is not None else None),
        "start_time": spans[0]["start_time_unix_nano"],
        "end_time": max(s["end_time_unix_nano"] for s in spans),
    }
    return result


def get_span(conn: sqlite3.Connection, span_id: str) -> SpanRecord | None:
    """Return a single span with its events."""
    row = conn.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,)).fetchone()
    if row is None:
        return None

    span = _span_record_from_row(row)
    _attach_events(conn, [span])
    return span
