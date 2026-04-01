"""Flow: /ycost traces.db integration."""

from __future__ import annotations

import json
import time

from tests.conftest import FOLK_QQ, MASTER_QQ, make_group_event
from tests.helpers import sent_texts
from tests.mocks import mock_recorder_api


async def _wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    worker = dispatcher._workers.get(key)
    if worker:
        await worker.queue.join()


def _seed_cost_trace(conn, *, agent: str, trace_id: str, amount: float, model: str = "test-model") -> None:
    now_ns = int(time.time() * 1_000_000_000)
    parent_span_id = f"{trace_id}-parent"
    child_span_id = f"{trace_id}-child"
    conn.execute(
        "INSERT INTO spans(span_id, parent_span_id, start_time_unix_nano, agent, trace_id) VALUES (?, NULL, ?, ?, ?)",
        (parent_span_id, now_ns, agent, trace_id),
    )
    conn.execute(
        "INSERT INTO spans(span_id, parent_span_id, start_time_unix_nano, agent, trace_id) VALUES (?, ?, ?, '', ?)",
        (child_span_id, parent_span_id, now_ns, trace_id),
    )
    conn.execute(
        "INSERT INTO events(span_id, name, attributes_json) VALUES (?, 'yuu.cost', ?)",
        (
            child_span_id,
            json.dumps(
                {
                    "yuu.cost.amount": amount,
                    "yuu.llm.model": model,
                }
            ),
        ),
    )
    conn.commit()


async def test_ycost_reads_shared_memory_traces_db(dispatcher, traces_db):
    conn, _uri = traces_db
    _seed_cost_trace(conn, agent="yuubot-main-1", trace_id="trace-1", amount=0.1234)
    _seed_cost_trace(conn, agent="yuubot-main-2", trace_id="trace-2", amount=0.5000)

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/ycost", user_id=MASTER_QQ, ctx_id=1))
        await _wait_worker(dispatcher, "group:1000")

    texts = sent_texts(sent)
    assert any("近 7 天开销" in text for text in texts)
    assert any("yuubot (main): $0.1234 / 1 次" in text for text in texts)
    assert all("$0.5000" not in text for text in texts)


async def test_ycost_all_requires_master(dispatcher):
    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/ycost --all", user_id=FOLK_QQ))
        await _wait_worker(dispatcher, "group:1000")

    assert any("--all 仅限 Master 使用" in text for text in sent_texts(sent))


async def test_ycost_all_aggregates_multiple_contexts(dispatcher, traces_db):
    conn, _uri = traces_db
    _seed_cost_trace(conn, agent="yuubot-main-1", trace_id="trace-1", amount=0.1234)
    _seed_cost_trace(conn, agent="yuubot-main-2", trace_id="trace-2", amount=0.5000)

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/ycost --all", user_id=MASTER_QQ, ctx_id=1))
        await _wait_worker(dispatcher, "group:1000")

    texts = sent_texts(sent)
    assert any("近 7 天开销 (全局):" in text for text in texts)
    assert any("yuubot (main): $0.6234 / 2 次" in text for text in texts)
