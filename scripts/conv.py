#!/usr/bin/env python3
"""Inspect a yuuagents conversation by ID.

Usage:
    python scripts/conv.py                        # list recent conversations
    python scripts/conv.py <conversation_id>      # show full conversation
    python scripts/conv.py <conversation_id> -n   # compact (no tool output)
    python scripts/conv.py --db /path/to/traces.db <conversation_id>

Reads from ~/.yagents/traces.db (yuuagents tracing DB).
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TRACES_DB = Path("~/.yagents/traces.db").expanduser()
TOOL_OUTPUT_LIMIT = 600


def open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"ERROR: traces.db not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.text_factory = str
    return conn


def ns_to_time(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%H:%M:%S")


def decode_tool_output(raw: str) -> str:
    """Tool output is stored as a JSON-encoded string (double-encoded)."""
    try:
        v = json.loads(raw)
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    except Exception:
        return raw


def fmt_tool_args(raw: str) -> str:
    try:
        args = json.loads(raw)
        parts = []
        for k, v in args.items():
            s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            parts.append(f"{k}={s[:80]!r}" if len(s) > 80 else f"{k}={s!r}")
        return ", ".join(parts)
    except Exception:
        return raw[:120]


def list_conversations(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT conversation_id,
               agent,
               model,
               MIN(start_time_unix_nano) AS first_ts,
               COUNT(CASE WHEN name = 'conversation' THEN 1 END) AS turns,
               COUNT(CASE WHEN name LIKE 'tool:%' THEN 1 END) AS tool_calls
        FROM spans
        WHERE conversation_id IS NOT NULL
        GROUP BY conversation_id
        ORDER BY first_ts DESC
        LIMIT 30
    """).fetchall()

    print(f"{'conversation_id':<38}  {'agent':<20}  {'model':<18}  {'time':>8}  turns  tools")
    print("-" * 110)
    for r in rows:
        t = ns_to_time(r["first_ts"])
        print(f"{r['conversation_id']:<38}  {(r['agent'] or '?'):<20}  {(r['model'] or '?'):<18}  {t:>8}  {r['turns']:>5}  {r['tool_calls']:>5}")


def show_conversation(conn: sqlite3.Connection, conv_id: str, show_tool_output: bool) -> None:
    spans = conn.execute("""
        SELECT span_id, parent_span_id, name, start_time_unix_nano, attributes_json
        FROM spans
        WHERE conversation_id = ?
        ORDER BY start_time_unix_nano
    """, (conv_id,)).fetchall()

    if not spans:
        sys.exit(f"ERROR: conversation not found: {conv_id}")

    # Fetch user events from conversation spans to interleave in timeline
    conv_span_ids = [r["span_id"] for r in spans if r["name"] == "conversation"]
    user_events: list[sqlite3.Row] = []
    if conv_span_ids:
        placeholders = ",".join("?" * len(conv_span_ids))
        user_events = conn.execute(
            f"SELECT span_id, name, time_unix_nano, attributes_json FROM events "
            f"WHERE span_id IN ({placeholders}) AND name = 'user' "
            f"ORDER BY time_unix_nano",
            conv_span_ids,
        ).fetchall()

    # Build merged timeline of spans and user events, sorted by timestamp
    timeline: list[tuple[int, str, sqlite3.Row]] = [
        (r["start_time_unix_nano"], "span", r) for r in spans
    ] + [
        (ev["time_unix_nano"], "user_event", ev) for ev in user_events
    ]
    timeline.sort(key=lambda x: x[0])

    meta_printed = False

    for ts_nano, kind, row in timeline:
        ts = ns_to_time(ts_nano)

        if kind == "user_event":
            attrs = json.loads(row["attributes_json"])
            print(f"[{ts}] USER")
            print(attrs.get("content", "").strip())
            print()
            continue

        name = row["name"]
        attrs = json.loads(row["attributes_json"])

        if name == "conversation":
            if not meta_printed:
                print(f"conversation: {conv_id}")
                print(f"agent:  {attrs.get('yuu.agent', '?')}")
                print(f"model:  {attrs.get('yuu.conversation.model', '?')}")
                print()
                meta_printed = True
            # user messages are now shown as events; skip this span's body

        elif name == "llm_gen":
            raw_items = attrs.get("yuu.llm_gen.items")
            if not raw_items:
                continue
            items = json.loads(raw_items)
            has_output = False
            for item in items:
                if item.get("type") == "text" and item.get("text", "").strip():
                    if not has_output:
                        print(f"[{ts}] ASSISTANT")
                        has_output = True
                    print(item["text"].strip())
                elif item.get("type") == "tool_calls":
                    if not has_output:
                        print(f"[{ts}] ASSISTANT")
                        has_output = True
                    for tc in item.get("tool_calls", []):
                        fn = tc.get("function", "?")
                        args = fmt_tool_args(json.dumps(tc.get("arguments", {})))
                        print(f"  → {fn}({args})")
            if has_output:
                print()

        elif name.startswith("tool:"):
            tool_name = attrs.get("yuu.tool.name", name[5:])
            raw_input = attrs.get("yuu.tool.input", "{}")
            raw_output = attrs.get("yuu.tool.output", "")

            tool_input = fmt_tool_args(raw_input)
            tool_output = decode_tool_output(raw_output)

            print(f"[{ts}] TOOL: {tool_name}({tool_input})")
            if show_tool_output and tool_output:
                if len(tool_output) > TOOL_OUTPUT_LIMIT:
                    tool_output = tool_output[:TOOL_OUTPUT_LIMIT] + f"... [{len(tool_output)} chars]"
                for line in tool_output.splitlines():
                    print(f"  {line}")
            print()

        # 'tools' grouping span and others: skip


def main() -> None:
    # Ensure UTF-8 output (important for CJK text)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Inspect yuuagents conversation traces")
    parser.add_argument("conv_id", nargs="?", help="conversation UUID to inspect")
    parser.add_argument("-n", "--no-output", action="store_true", help="hide tool output (compact view)")
    parser.add_argument("--db", default=str(TRACES_DB), help="path to traces.db")
    args = parser.parse_args()

    conn = open_db(Path(args.db))

    if args.conv_id is None:
        list_conversations(conn)
    else:
        show_conversation(conn, args.conv_id, show_tool_output=not args.no_output)


if __name__ == "__main__":
    main()
