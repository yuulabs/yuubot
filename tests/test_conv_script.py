from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
from pathlib import Path


def _load_conv_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "conv.py"
    spec = importlib.util.spec_from_file_location("conv_script", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


conv = _load_conv_module()


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE spans (
            trace_id TEXT NOT NULL DEFAULT '',
            span_id TEXT NOT NULL DEFAULT '',
            parent_span_id TEXT,
            name TEXT NOT NULL,
            start_time_unix_nano INTEGER NOT NULL,
            end_time_unix_nano INTEGER NOT NULL DEFAULT 0,
            status_code INTEGER NOT NULL DEFAULT 0,
            status_message TEXT NOT NULL DEFAULT '',
            attributes_json TEXT NOT NULL DEFAULT '{}',
            conversation_id TEXT,
            agent TEXT,
            model TEXT,
            resource_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    return conn


def _insert_conversation(
    conn: sqlite3.Connection,
    *,
    conv_id: str,
    runtime_agent: str,
    sort_key: int,
) -> None:
    rows = [
        (
            f"trace-{conv_id}",
            f"span-{conv_id}-root",
            None,
            "conversation",
            sort_key,
            json.dumps(
                {
                    "yuu.agent": "main",
                    "yuu.conversation.model": "test-model",
                }
            ),
            conv_id,
            runtime_agent,
            "test-model",
        ),
        (
            f"trace-{conv_id}",
            f"span-{conv_id}-turn",
            f"span-{conv_id}-root",
            "turn",
            sort_key + 1,
            json.dumps({"yuu.turn.role": "user", "yuu.turn.items": json.dumps([{"type": "text", "text": "hello"}])}),
            conv_id,
            runtime_agent,
            "test-model",
        ),
        (
            f"trace-{conv_id}",
            f"span-{conv_id}-tool",
            f"span-{conv_id}-root",
            "tool:im",
            sort_key + 2,
            json.dumps({"yuu.tool.name": "im send", "yuu.tool.output": json.dumps("ok", ensure_ascii=False)}),
            conv_id,
            runtime_agent,
            "test-model",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO spans (
            trace_id, span_id, parent_span_id, name, start_time_unix_nano,
            attributes_json, conversation_id, agent, model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def _run_conv(*args: str) -> str:
    argv = ["conv.py", *args]
    buf = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(buf):
            conv.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


def test_conv_script_lists_only_matching_ctx(tmp_path) -> None:
    db_path = tmp_path / "traces.db"
    conn = _make_db(db_path)
    _insert_conversation(conn, conv_id="alpha7777", runtime_agent="yuubot-main-7", sort_key=100)
    _insert_conversation(conn, conv_id="bravo4242", runtime_agent="agent-main-42", sort_key=200)
    _insert_conversation(conn, conv_id="delta9999", runtime_agent="delegate-ops-abcd1234", sort_key=300)
    conn.close()

    output = _run_conv("--db", str(db_path), "--ctx", "42")

    assert "bravo424" in output
    assert "alpha777" not in output
    assert "delta999" not in output


def test_conv_script_last_uses_ctx_filter(tmp_path) -> None:
    db_path = tmp_path / "traces.db"
    conn = _make_db(db_path)
    _insert_conversation(conn, conv_id="alpha7777", runtime_agent="yuubot-main-7", sort_key=100)
    _insert_conversation(conn, conv_id="bravo4242", runtime_agent="yuubot-main-42", sort_key=200)
    conn.close()

    output = _run_conv("--db", str(db_path), "--ctx", "42", "--last")

    assert "conversation: bravo424" in output
    assert "im send" in output
