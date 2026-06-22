"""Tests for the yuutrace usage-analytics query layer.

Covers:
- ``yuutrace.cli.db.get_usage_summary`` / ``get_latency_stats`` /
  ``get_tool_call_counts`` / ``get_phase_breakdown`` — called with a
  pre-populated SQLite connection.
- ``yuutrace.cli.ui`` REST routes — exercised via Starlette ``TestClient``
  against a temp DB.

The fixture DB is built directly with ``init_db`` + raw inserts of spans and
events. No live OTEL collector or LLM provider is involved.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from yuutrace.cli import db as dbmod
from yuutrace.cli.ui import _build_app

# ---------------------------------------------------------------------------
# Fixture DB
# ---------------------------------------------------------------------------

# One nanosecond = 1
_NS = 1
_US = 1_000
_MS = 1_000_000
_S = 1_000_000_000


def _now_ns() -> int:
    return time.time_ns()


def _insert_span(
    conn: sqlite3.Connection,
    *,
    span_id: str,
    name: str,
    start_ns: int,
    end_ns: int,
    parent_span_id: str | None = None,
    attributes: dict[str, object] | None = None,
    conversation_id: str = "conv-fixture",
    agent: str = "fixture-agent",
    model: str = "fixture-model",
) -> None:
    conn.execute(
        """INSERT INTO spans
           (trace_id, span_id, parent_span_id, name,
            start_time_unix_nano, end_time_unix_nano,
            status_code, status_message, attributes_json,
            conversation_id, agent, model, resource_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "trace-fixture",
            span_id,
            parent_span_id,
            name,
            start_ns,
            end_ns,
            0,
            None,
            json.dumps(attributes or {}, ensure_ascii=False),
            conversation_id,
            agent,
            model,
            "{}",
        ),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    span_id: str,
    name: str,
    time_ns: int,
    attributes: dict[str, object] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO events
           (span_id, name, time_unix_nano, attributes_json)
           VALUES (?, ?, ?, ?)""",
        (
            span_id,
            name,
            time_ns,
            json.dumps(attributes or {}, ensure_ascii=False),
        ),
    )


def _build_fixture_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a small known trace DB.

    Layout (one conversation, one turn, two entities, one tool call):

    - conversation span (root)
      - turn span
        - entity "thinking" (entity_id=e1)
          + entity.end (entity_id=e1)
        - entity "text" (entity_id=e2)
          + entity.end (entity_id=e2)
        - entity "tool_call" (entity_id=e3)
          + entity.end (entity_id=e3)
        - event: tool.result_appended (tool_name=bash)
        - event: yuu.llm.usage
        - event: yuu.cost
    """
    db_path = str(tmp_path / "traces-fixture.db")
    conn = dbmod.init_db(db_path)

    # Anchor times one minute in the past so that any ``time.time_ns()``
    # measured later at request time is strictly greater than every event
    # time we insert. This keeps ``range=day``/``range=total`` requests
    # deterministic regardless of test-vs-fixture timing drift.
    base = _now_ns() - 60 * _S
    # Lay out times so durations are easy to assert.
    conv_start = base
    conv_end = base + 1 * _S

    turn_start = base + 10 * _MS
    turn_end = base + 900 * _MS

    _insert_span(
        conn,
        span_id="conv-root",
        name="conversation",
        start_ns=conv_start,
        end_ns=conv_end,
    )

    _insert_span(
        conn,
        span_id="turn-1",
        name="turn",
        start_ns=turn_start,
        end_ns=turn_end,
        parent_span_id="conv-root",
        attributes={"yuu.turn.role": "assistant"},
    )

    # Entity: thinking — duration 50 ms
    _insert_span(
        conn,
        span_id="ent-thinking",
        name="entity",
        start_ns=turn_start + 1 * _MS,
        end_ns=turn_start + 1 * _MS,  # point marker
        parent_span_id="turn-1",
        attributes={"yuu.entity.id": "e1", "yuu.entity.type": "thinking"},
    )
    _insert_span(
        conn,
        span_id="ent-thinking-end",
        name="entity.end",
        start_ns=turn_start + 51 * _MS,
        end_ns=turn_start + 51 * _MS,
        parent_span_id="turn-1",
        attributes={"yuu.entity.id": "e1", "yuu.entity.type": "thinking"},
    )

    # Entity: text — duration 80 ms
    _insert_span(
        conn,
        span_id="ent-text",
        name="entity",
        start_ns=turn_start + 52 * _MS,
        end_ns=turn_start + 52 * _MS,
        parent_span_id="turn-1",
        attributes={"yuu.entity.id": "e2", "yuu.entity.type": "text"},
    )
    _insert_span(
        conn,
        span_id="ent-text-end",
        name="entity.end",
        start_ns=turn_start + 132 * _MS,
        end_ns=turn_start + 132 * _MS,
        parent_span_id="turn-1",
        attributes={"yuu.entity.id": "e2", "yuu.entity.type": "text"},
    )

    # Entity: tool_call — duration 40 ms
    _insert_span(
        conn,
        span_id="ent-tool",
        name="entity",
        start_ns=turn_start + 133 * _MS,
        end_ns=turn_start + 133 * _MS,
        parent_span_id="turn-1",
        attributes={"yuu.entity.id": "e3", "yuu.entity.type": "tool_call"},
    )
    _insert_span(
        conn,
        span_id="ent-tool-end",
        name="entity.end",
        start_ns=turn_start + 173 * _MS,
        end_ns=turn_start + 173 * _MS,
        parent_span_id="turn-1",
        attributes={"yuu.entity.id": "e3", "yuu.entity.type": "tool_call"},
    )

    # Events on turn-1
    llm_usage_time = turn_start + 250 * _MS
    tool_result_time = turn_start + 400 * _MS

    _insert_event(
        conn,
        span_id="turn-1",
        name="llm.started",
        time_ns=turn_start + 5 * _MS,
    )

    # First "output" of any kind happens at thinking entity start: turn_start + 1ms
    # → first_token_latency_ms ≈ 1.0

    _insert_event(
        conn,
        span_id="turn-1",
        name="tool.result_appended",
        time_ns=tool_result_time,
        attributes={"yuu.event.tool_name": "bash"},
    )

    _insert_event(
        conn,
        span_id="turn-1",
        name="yuu.llm.usage",
        time_ns=llm_usage_time,
        attributes={
            "yuu.llm.usage.input_tokens": 1000,
            "yuu.llm.usage.output_tokens": 200,
            "yuu.llm.usage.cache_read_tokens": 300,
            "yuu.llm.usage.cache_write_tokens": 0,
            "yuu.llm.usage.total_tokens": 1200,
            "yuu.llm.provider": "fixture",
            "yuu.llm.model": "fixture-model",
        },
    )

    _insert_event(
        conn,
        span_id="turn-1",
        name="yuu.cost",
        time_ns=llm_usage_time + 1,
        attributes={
            "yuu.cost.amount": 0.042,
            "yuu.cost.category": "llm",
            "yuu.cost.currency": "USD",
        },
    )

    conn.commit()
    return conn


@pytest.fixture
def fixture_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = _build_fixture_db(tmp_path)
    yield conn
    conn.close()


@pytest.fixture
def fixture_db_path(tmp_path: Path) -> str:
    # Build the fixture DB to a path, then close it so the UI server can
    # reopen it through init_db.
    conn = _build_fixture_db(tmp_path)
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    conn.close()
    return db_path


@pytest.fixture
def ui_client(fixture_db_path: str) -> TestClient:
    app = _build_app(fixture_db_path)
    return TestClient(app)


# ---------------------------------------------------------------------------
# get_usage_summary
# ---------------------------------------------------------------------------


def test_get_usage_summary_aggregates_cost_and_tokens(fixture_conn: sqlite3.Connection) -> None:
    result = dbmod.get_usage_summary(
        fixture_conn, start_ns=0, end_ns=_now_ns() + _S
    )
    assert set(result) == {
        "cost",
        "requests",
        "input_tokens_uncached",
        "cached_input_tokens",
        "output_tokens",
    }
    assert result["cost"] == pytest.approx(0.042)
    assert result["requests"] == 1
    # input_tokens(1000) - cache_read(300) = 700 uncached
    assert result["input_tokens_uncached"] == 700
    assert result["cached_input_tokens"] == 300
    assert result["output_tokens"] == 200


def test_get_usage_summary_empty_range_returns_zero(fixture_conn: sqlite3.Connection) -> None:
    # Range far in the future → no events match.
    future_start = _now_ns() + 10 * _S
    result = dbmod.get_usage_summary(
        fixture_conn, start_ns=future_start, end_ns=future_start + _S
    )
    assert result["cost"] == 0.0
    assert result["requests"] == 0
    assert result["input_tokens_uncached"] == 0
    assert result["cached_input_tokens"] == 0
    assert result["output_tokens"] == 0


# ---------------------------------------------------------------------------
# get_tool_call_counts
# ---------------------------------------------------------------------------


def test_get_tool_call_counts_groups_by_name(fixture_conn: sqlite3.Connection) -> None:
    result = dbmod.get_tool_call_counts(
        fixture_conn, start_ns=0, end_ns=_now_ns() + _S
    )
    assert result == [{"tool_name": "bash", "count": 1}]


def test_get_tool_call_counts_empty(fixture_conn: sqlite3.Connection) -> None:
    future_start = _now_ns() + 10 * _S
    result = dbmod.get_tool_call_counts(
        fixture_conn, start_ns=future_start, end_ns=future_start + _S
    )
    assert result == []


# ---------------------------------------------------------------------------
# get_latency_stats
# ---------------------------------------------------------------------------


def test_get_latency_stats_reports_turn_and_first_token(fixture_conn: sqlite3.Connection) -> None:
    result = dbmod.get_latency_stats(
        fixture_conn, start_ns=0, end_ns=_now_ns() + _S
    )
    assert set(result) == {
        "avg_first_token_latency_ms",
        "avg_turn_time_ms",
        "avg_tool_execution_time_ms",
        "tool_execution_samples",
    }
    # turn duration = 900ms - 10ms = 890ms; one turn → avg 890
    assert result["avg_turn_time_ms"] == pytest.approx(890.0, abs=1.0)
    # first token ≈ thinking entity start - turn start = 1ms
    assert result["avg_first_token_latency_ms"] == pytest.approx(1.0, abs=1.0)
    # tool_execution = max(tool.result_appended.time) - llm.usage.time
    #   = (turn_start+400ms) - (turn_start+250ms) = 150ms
    assert result["avg_tool_execution_time_ms"] == pytest.approx(150.0, abs=1.0)
    assert result["tool_execution_samples"] == 1


def test_get_latency_stats_no_turns(fixture_conn: sqlite3.Connection) -> None:
    future_start = _now_ns() + 10 * _S
    result = dbmod.get_latency_stats(
        fixture_conn, start_ns=future_start, end_ns=future_start + _S
    )
    assert result["avg_turn_time_ms"] == 0.0
    assert result["avg_first_token_latency_ms"] == 0.0
    assert result["avg_tool_execution_time_ms"] == 0.0
    assert result["tool_execution_samples"] == 0


# ---------------------------------------------------------------------------
# get_phase_breakdown
# ---------------------------------------------------------------------------


def test_get_phase_breakdown_reports_each_phase(fixture_conn: sqlite3.Connection) -> None:
    result = dbmod.get_phase_breakdown(
        fixture_conn, start_ns=0, end_ns=_now_ns() + _S
    )
    assert set(result) == {
        "thinking_time_ms",
        "text_time_ms",
        "tool_call_time_ms",
        "tool_execution_time_ms",
    }
    assert result["thinking_time_ms"] == pytest.approx(50.0, abs=1.0)
    assert result["text_time_ms"] == pytest.approx(80.0, abs=1.0)
    assert result["tool_call_time_ms"] == pytest.approx(40.0, abs=1.0)
    assert result["tool_execution_time_ms"] == pytest.approx(150.0, abs=1.0)


def test_get_phase_breakdown_no_turns(fixture_conn: sqlite3.Connection) -> None:
    future_start = _now_ns() + 10 * _S
    result = dbmod.get_phase_breakdown(
        fixture_conn, start_ns=future_start, end_ns=future_start + _S
    )
    assert result["thinking_time_ms"] == 0.0
    assert result["text_time_ms"] == 0.0
    assert result["tool_call_time_ms"] == 0.0
    assert result["tool_execution_time_ms"] == 0.0


# ---------------------------------------------------------------------------
# time_range helper
# ---------------------------------------------------------------------------


def test_time_range_day_starts_at_midnight() -> None:
    start_ns, end_ns = dbmod.time_range("day")
    assert end_ns >= start_ns
    # start should be near midnight UTC of today
    import datetime as dt

    midnight = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    expected_start_ns = int(midnight.timestamp() * _S)
    assert start_ns == expected_start_ns


def test_time_range_total_covers_all_history() -> None:
    start_ns, end_ns = dbmod.time_range("total")
    assert start_ns == 0
    assert end_ns > 0


def test_time_range_week_is_rolling_seven_days() -> None:
    start_ns, end_ns = dbmod.time_range("week")
    delta_ns = end_ns - start_ns
    assert delta_ns == pytest.approx(7 * 86_400 * _S, rel=1e-6)


@pytest.mark.parametrize("period", ["day", "week", "month", "year", "total"])
def test_time_range_accepts_all_documented_periods(period: str) -> None:
    start_ns, end_ns = dbmod.time_range(period)
    assert end_ns >= start_ns


# ---------------------------------------------------------------------------
# API routes (via Starlette TestClient)
# ---------------------------------------------------------------------------


def test_api_usage_summary_returns_expected_keys(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/summary?range=total")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "cost",
        "requests",
        "input_tokens_uncached",
        "cached_input_tokens",
        "output_tokens",
    }
    assert body["cost"] == pytest.approx(0.042)
    assert body["requests"] == 1
    assert body["input_tokens_uncached"] == 700
    assert body["cached_input_tokens"] == 300
    assert body["output_tokens"] == 200


def test_api_usage_summary_day_range_returns_200(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/summary?range=day")
    assert resp.status_code == 200
    body = resp.json()
    # The fixture was inserted with "now" timestamps so day should match.
    assert body["requests"] == 1


def test_api_usage_latency_returns_expected_keys(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/latency?range=total")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "avg_first_token_latency_ms",
        "avg_turn_time_ms",
        "avg_tool_execution_time_ms",
        "tool_execution_samples",
    }
    assert body["tool_execution_samples"] == 1


def test_api_usage_tools_returns_counts(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/tools?range=total")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"tool_name": "bash", "count": 1}]


def test_api_usage_phases_returns_expected_keys(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/phases?range=day")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "thinking_time_ms",
        "text_time_ms",
        "tool_call_time_ms",
        "tool_execution_time_ms",
    }


def test_api_usage_phases_year_returns_400(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/phases?range=year")
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    assert "year" in body["error"].lower() or "total" in body["error"].lower()


def test_api_usage_phases_total_returns_400(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/phases?range=total")
    assert resp.status_code == 400


def test_api_usage_summary_rejects_invalid_range(ui_client: TestClient) -> None:
    resp = ui_client.get("/api/usage/summary?range=hour")
    assert resp.status_code == 400
