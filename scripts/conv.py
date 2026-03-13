#!/usr/bin/env python3
"""Inspect yuuagents conversation traces.

Usage:
    python scripts/conv.py                   # list recent conversations
    python scripts/conv.py -l                # show the latest conversation
    python scripts/conv.py <id-or-prefix>    # show full conversation
    python scripts/conv.py <id> -n           # compact: collapse tool calls
    python scripts/conv.py --agent main      # filter list by agent
    python scripts/conv.py --limit 10        # show last N conversations

Reads from ~/.yagents/traces.db (yuuagents tracing DB).
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

TRACES_DB = Path("~/.yagents/traces.db").expanduser()
TOOL_OUTPUT_LIMIT = 600


class _SupportsReconfigure(Protocol):
    def reconfigure(self, *, encoding: str) -> None: ...


def open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"ERROR: traces.db not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.text_factory = str
    return conn


def ns_to_dt(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9).strftime("%m-%d %H:%M")


def ns_to_time(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9).strftime("%H:%M:%S")


def decode_tool_output(raw: str) -> str:
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


def resolve_id(conn: sqlite3.Connection, prefix: str) -> str:
    """Resolve a full or partial conversation ID."""
    rows = conn.execute(
        "SELECT DISTINCT conversation_id FROM spans "
        "WHERE conversation_id LIKE ? LIMIT 2",
        (f"{prefix}%",),
    ).fetchall()
    if not rows:
        sys.exit(f"ERROR: no conversation matching prefix: {prefix!r}")
    if len(rows) > 1:
        sys.exit(f"ERROR: prefix {prefix!r} is ambiguous — {[r[0] for r in rows]}")
    return rows[0][0]


def latest_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT conversation_id FROM spans WHERE conversation_id IS NOT NULL "
        "ORDER BY start_time_unix_nano DESC LIMIT 1"
    ).fetchone()
    if not row:
        sys.exit("ERROR: no conversations found")
    return row[0]


def list_conversations(conn: sqlite3.Connection, agent: str | None, limit: int) -> None:
    query = """
        SELECT conversation_id,
               agent,
               model,
               MIN(start_time_unix_nano) AS first_ts,
               COUNT(CASE WHEN name = 'conversation' THEN 1 END) AS turns,
               COUNT(CASE WHEN name LIKE 'tool:%' THEN 1 END) AS tool_calls
        FROM spans
        WHERE conversation_id IS NOT NULL
        {agent_filter}
        GROUP BY conversation_id
        ORDER BY first_ts DESC
        LIMIT ?
    """
    if agent:
        rows = conn.execute(
            query.format(agent_filter="AND agent = ?"), (agent, limit)
        ).fetchall()
    else:
        rows = conn.execute(query.format(agent_filter=""), (limit,)).fetchall()

    print(f"{'id':>8}  {'agent':<16}  {'model':<18}  {'time':<13}  turns  tools")
    print("-" * 72)
    for r in rows:
        short = (r["conversation_id"] or "")[:8]
        dt = ns_to_dt(r["first_ts"])
        agent_s = (r["agent"] or "?")[:16]
        model_s = (r["model"] or "?")[:18]
        print(f"{short}  {agent_s:<16}  {model_s:<18}  {dt:<13}  {r['turns']:>5}  {r['tool_calls']:>5}")


def show_conversation(conn: sqlite3.Connection, conv_id: str, compact: bool) -> None:
    spans = conn.execute(
        "SELECT span_id, parent_span_id, name, start_time_unix_nano, attributes_json "
        "FROM spans WHERE conversation_id = ? ORDER BY start_time_unix_nano",
        (conv_id,),
    ).fetchall()

    if not spans:
        sys.exit(f"ERROR: conversation not found: {conv_id}")

    conv_span_ids = [r["span_id"] for r in spans if r["name"] == "conversation"]
    user_events: list[sqlite3.Row] = []
    if conv_span_ids:
        placeholders = ",".join("?" * len(conv_span_ids))
        user_events = conn.execute(
            f"SELECT span_id, name, time_unix_nano, attributes_json FROM events "
            f"WHERE span_id IN ({placeholders}) AND name = 'user' ORDER BY time_unix_nano",
            conv_span_ids,
        ).fetchall()

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
                short = conv_id[:8]
                print(f"conversation: {short}  ({conv_id})")
                print(f"agent: {attrs.get('yuu.agent', '?')}  model: {attrs.get('yuu.conversation.model', '?')}")
                print()
                meta_printed = True

        elif name == "llm_gen":
            raw_items = attrs.get("yuu.llm_gen.items")
            if not raw_items:
                continue
            items = json.loads(raw_items)
            has_output = False
            tool_call_count = 0
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
                    if not compact:
                        for tc in item.get("tool_calls", []):
                            fn = tc.get("function", "?")
                            args = fmt_tool_args(json.dumps(tc.get("arguments", {})))
                            print(f"  → {fn}({args})")
                    else:
                        tool_call_count += len(item.get("tool_calls", []))
            if compact and tool_call_count:
                print(f"  ({tool_call_count} tool call{'s' if tool_call_count > 1 else ''})")
            if has_output:
                print()

        elif name.startswith("tool:") and not compact:
            tool_name = attrs.get("yuu.tool.name", name[5:])
            raw_input = attrs.get("yuu.tool.input", "{}")
            raw_output = attrs.get("yuu.tool.output", "")

            tool_input = fmt_tool_args(raw_input)
            tool_output = decode_tool_output(raw_output)

            print(f"[{ts}] TOOL: {tool_name}({tool_input})")
            if tool_output:
                if len(tool_output) > TOOL_OUTPUT_LIMIT:
                    tool_output = tool_output[:TOOL_OUTPUT_LIMIT] + f"... [{len(tool_output)} chars]"
                for line in tool_output.splitlines():
                    print(f"  {line}")
            print()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        cast(_SupportsReconfigure, sys.stdout).reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Inspect yuuagents conversation traces")
    parser.add_argument("conv_id", nargs="?", help="conversation ID or prefix")
    parser.add_argument("-l", "--last", action="store_true", help="show most recent conversation")
    parser.add_argument("-n", "--compact", action="store_true", help="collapse tool calls (no tool output)")
    parser.add_argument("--agent", help="filter list by agent name")
    parser.add_argument("--limit", type=int, default=20, help="max conversations to list (default 20)")
    parser.add_argument("--db", default=str(TRACES_DB), help="path to traces.db")
    args = parser.parse_args()

    conn = open_db(Path(args.db))

    if args.last or args.conv_id:
        conv_id = latest_id(conn) if args.last else resolve_id(conn, args.conv_id)
        show_conversation(conn, conv_id, compact=args.compact)
    else:
        list_conversations(conn, agent=args.agent, limit=args.limit)


if __name__ == "__main__":
    main()
