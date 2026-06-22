"""Tests for ``yuubot.core.cost_guard.DailyBudgetGuard``.

Builds a fixture ``traces.db`` against the live yuutrace schema and inserts
``yuu.cost`` events to verify the daily-limit gate. No live LLM provider
calls; no schema changes — the schema is created via ``yuutrace.cli.db.init_db``.
"""

from __future__ import annotations

import time

import pytest

from yuubot.core.cost_guard import DailyBudgetGuard
from yuutrace.cli.db import init_db

_COST_AMOUNT_ATTR = '$."yuu.cost.amount"'


def _insert_cost_event(
    conn,
    *,
    span_id: str,
    amount: float,
    when_ns: int | None = None,
) -> None:
    """Insert a span + a ``yuu.cost`` event carrying ``yuu.cost.amount``.

    These are the rows ``get_usage_summary`` aggregates. The span exists only
    to satisfy the ``events.span_id`` foreign key; the analytics query joins
    on ``events`` directly, so the span's own attributes are irrelevant.
    """
    when = when_ns if when_ns is not None else time.time_ns()
    conn.execute(
        """INSERT INTO spans
             (trace_id, span_id, parent_span_id, name,
              start_time_unix_nano, end_time_unix_nano,
              status_code, status_message, attributes_json,
              conversation_id, agent, model, resource_json)
           VALUES (?, ?, NULL, ?, ?, ?, 0, NULL, '{}', NULL, NULL, NULL, '{}')""",
        (f"trace-{span_id}", span_id, "stub", when, when + 1_000_000),
    )
    conn.execute(
        """INSERT INTO events (span_id, name, time_unix_nano, attributes_json)
           VALUES (?, 'yuu.cost', ?, json(?))""",
        (span_id, when, f'{{"yuu.cost.amount": {amount}}}'),
    )
    conn.commit()


def test_disabled_guard_never_exceeds(tmp_path) -> None:
    db_path = str(tmp_path / "traces.db")
    conn = init_db(db_path)
    _insert_cost_event(conn, span_id="s1", amount=10_000.0)
    conn.close()

    guard = DailyBudgetGuard(traces_db_path=db_path, daily_limit_usd=0.0)
    # limit <= 0 → fast-path False without touching the DB at all.
    assert guard.is_exceeded() is False
    assert guard.current_cost == 0.0
    assert guard.limit == 0.0


def test_below_limit_allows_send(tmp_path) -> None:
    db_path = str(tmp_path / "traces.db")
    conn = init_db(db_path)
    _insert_cost_event(conn, span_id="s1", amount=3.0)
    conn.close()

    guard = DailyBudgetGuard(traces_db_path=db_path, daily_limit_usd=5.0)
    assert guard.is_exceeded() is False
    assert pytest.approx(guard.current_cost, rel=1e-9) == 3.0


def test_at_or_above_limit_blocks_send(tmp_path) -> None:
    db_path = str(tmp_path / "traces.db")
    conn = init_db(db_path)
    _insert_cost_event(conn, span_id="s1", amount=6.0)
    conn.close()

    guard = DailyBudgetGuard(traces_db_path=db_path, daily_limit_usd=5.0)
    assert guard.is_exceeded() is True
    # send handler exposes limit + current spend in the 402 body
    assert guard.limit == 5.0
    assert pytest.approx(guard.current_cost, rel=1e-9) == 6.0


def test_costs_outside_today_window_are_ignored(tmp_path) -> None:
    db_path = str(tmp_path / "traces.db")
    # yesterday (outside time_range("day") = today 00:00 UTC → now)
    day_ns = 86_400 * 1_000_000_000
    yesterday = time.time_ns() - day_ns
    conn = init_db(db_path)
    _insert_cost_event(conn, span_id="old", amount=1_000.0, when_ns=yesterday)
    _insert_cost_event(conn, span_id="now", amount=4.0)
    conn.close()

    guard = DailyBudgetGuard(traces_db_path=db_path, daily_limit_usd=5.0)
    assert guard.is_exceeded() is False
    assert pytest.approx(guard.current_cost, rel=1e-9) == 4.0


def test_send_handler_returns_402_when_budget_exceeded(tmp_path) -> None:
    """End-to-end check that the daemon send handler honours the guard."""
    import json

    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from yuubot.core.cost_guard import DailyBudgetGuard
    from yuubot.runtime.daemon.handlers import make_send_conversation_message_handler

    db_path = str(tmp_path / "traces.db")
    conn = init_db(db_path)
    _insert_cost_event(conn, span_id="s1", amount=6.0)
    conn.close()

    guard = DailyBudgetGuard(traces_db_path=db_path, daily_limit_usd=5.0)
    handler = make_send_conversation_message_handler(
        conversation_manager=None,  # type: ignore[arg-type]
        daily_guard=guard,
    )
    app = Starlette(
        routes=[
            Route(
                "/api/admin/conversations/{conversation_id}/messages",
                handler,
                methods=("POST",),
            ),
        ]
    )
    client = TestClient(app)
    response = client.post(
        "/api/admin/conversations/abc/messages",
        json={
            "actor_id": "actor-1",
            "text": "hello",
        },
    )
    assert response.status_code == 402
    body = json.loads(response.content)
    assert body["status"] == "error"
    assert body["code"] == "budget_exceeded"
    assert body["limit"] == 5.0
    assert pytest.approx(body["spent"], rel=1e-9) == 6.0


def test_send_handler_allows_when_budget_under_limit(tmp_path) -> None:
    """When the guard is under its ceiling the send passes the gate.

    We only assert that the gate is *not* the thing blocking the request.
    The handler short-circuits before any conversation-manager interaction,
    so with ``conversation_manager=None`` it will raise on the next line
    (parsing the body) — but critically the response will NOT be a 402 from
    the budget gate. We assert the response status is not 402.
    """
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from yuubot.core.cost_guard import DailyBudgetGuard
    from yuubot.runtime.daemon.handlers import make_send_conversation_message_handler

    db_path = str(tmp_path / "traces.db")
    conn = init_db(db_path)
    _insert_cost_event(conn, span_id="s1", amount=3.0)
    conn.close()

    guard = DailyBudgetGuard(traces_db_path=db_path, daily_limit_usd=5.0)
    handler = make_send_conversation_message_handler(
        conversation_manager=None,  # type: ignore[arg-type]
        daily_guard=guard,
    )
    app = Starlette(
        routes=[
            Route(
                "/api/admin/conversations/{conversation_id}/messages",
                handler,
                methods=("POST",),
            ),
        ]
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/admin/conversations/abc/messages",
        json={"actor_id": "actor-1", "text": "hello"},
    )
    # The budget gate did not fire (under the limit). The downstream
    # conversation_manager=None call raises AttributeError → 500, but never
    # the 402 from the guard. The acceptance criterion is specifically:
    # "does not block any send when spend is under the limit".
    assert response.status_code != 402
