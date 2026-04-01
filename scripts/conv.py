#!/usr/bin/env python3
"""Inspect yuuagents conversation traces.

Usage:
    python scripts/conv.py                        # list recent conversations
    python scripts/conv.py -l                     # show the latest conversation
    python scripts/conv.py --ctx 12              # only conversations for ctx 12
    python scripts/conv.py <id-or-prefix>         # show full conversation
    python scripts/conv.py <id> -n                # compact: collapse tool calls
    python scripts/conv.py <id> --tool im         # only show tool calls matching "im"
    python scripts/conv.py <id> --full            # no truncation on tool I/O
    python scripts/conv.py <id> --grep "飞越"     # highlight/filter lines matching pattern
    python scripts/conv.py --agent main           # filter list by agent
    python scripts/conv.py --limit 10             # show last N conversations

Reads from ~/.yagents/traces.db (yuuagents tracing DB).
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

TRACES_DB = Path("~/.yagents/traces.db").expanduser()
TOOL_OUTPUT_LIMIT = 600
TOOL_INPUT_ARG_LIMIT = 120

# ANSI colors
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"
BG_YELLOW = "\033[43m"
FG_BLACK = "\033[30m"


class _SupportsReconfigure(Protocol):
    def reconfigure(self, *, encoding: str) -> None: ...


def _no_color() -> None:
    global DIM, BOLD, CYAN, GREEN, YELLOW, RED, MAGENTA, RESET, BG_YELLOW, FG_BLACK
    DIM = BOLD = CYAN = GREEN = YELLOW = RED = MAGENTA = RESET = ""
    BG_YELLOW = FG_BLACK = ""


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


def fmt_tool_args(raw: str, limit: int = TOOL_INPUT_ARG_LIMIT) -> str:
    try:
        args = json.loads(raw)
        parts = []
        for k, v in args.items():
            s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            parts.append(f"{k}={s[:limit]!r}" if len(s) > limit else f"{k}={s!r}")
        return ", ".join(parts)
    except Exception:
        return raw[:limit]


def highlight(text: str, pattern: re.Pattern | None) -> str:
    if not pattern:
        return text
    return pattern.sub(lambda m: f"{BG_YELLOW}{FG_BLACK}{m.group()}{RESET}", text)


def _ctx_agent_like_patterns(ctx_id: int) -> tuple[str, ...]:
    return (
        f"agent-%-{ctx_id}",
        f"yuubot-%-{ctx_id}",
    )


def _ctx_filter_sql(ctx_id: int | None, column: str = "agent") -> tuple[str, tuple[str, ...]]:
    if ctx_id is None:
        return "", ()
    patterns = _ctx_agent_like_patterns(ctx_id)
    clause = " AND (" + " OR ".join(f"{column} LIKE ?" for _ in patterns) + ")"
    return clause, patterns


def resolve_id(conn: sqlite3.Connection, prefix: str, *, ctx_id: int | None = None) -> str:
    ctx_clause, ctx_params = _ctx_filter_sql(ctx_id)
    rows = conn.execute(
        "SELECT DISTINCT conversation_id FROM spans "
        f"WHERE conversation_id LIKE ?{ctx_clause} LIMIT 2",
        (f"{prefix}%", *ctx_params),
    ).fetchall()
    if not rows:
        detail = f" for ctx {ctx_id}" if ctx_id is not None else ""
        sys.exit(f"ERROR: no conversation matching prefix{detail}: {prefix!r}")
    if len(rows) > 1:
        sys.exit(f"ERROR: prefix {prefix!r} is ambiguous — {[r[0] for r in rows]}")
    return rows[0][0]


def latest_id(conn: sqlite3.Connection, *, ctx_id: int | None = None) -> str:
    ctx_clause, ctx_params = _ctx_filter_sql(ctx_id)
    row = conn.execute(
        "SELECT conversation_id FROM spans WHERE conversation_id IS NOT NULL "
        f"{ctx_clause} ORDER BY start_time_unix_nano DESC LIMIT 1",
        ctx_params,
    ).fetchone()
    if not row:
        detail = f" for ctx {ctx_id}" if ctx_id is not None else ""
        sys.exit(f"ERROR: no conversations found{detail}")
    return row[0]


def list_conversations(
    conn: sqlite3.Connection,
    agent: str | None,
    limit: int,
    *,
    ctx_id: int | None = None,
) -> None:
    ctx_clause, ctx_params = _ctx_filter_sql(ctx_id)
    query = """
        SELECT conversation_id,
               agent,
               model,
               MIN(start_time_unix_nano) AS first_ts,
               COUNT(CASE WHEN name = 'turn' AND json_extract(attributes_json, '$."yuu.turn.role"') = 'user' THEN 1 END) AS user_turns,
               COUNT(CASE WHEN name LIKE 'tool:%' THEN 1 END) AS tool_calls
        FROM spans
        WHERE conversation_id IS NOT NULL
        {agent_filter}
        {ctx_filter}
        GROUP BY conversation_id
        ORDER BY first_ts DESC
        LIMIT ?
    """
    if agent:
        rows = conn.execute(
            query.format(agent_filter="AND agent = ?", ctx_filter=ctx_clause),
            (agent, *ctx_params, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            query.format(agent_filter="", ctx_filter=ctx_clause),
            (*ctx_params, limit),
        ).fetchall()

    print(f"{'id':>8}  {'agent':<16}  {'model':<18}  {'time':<13}  {'user':>5}  {'tools':>5}")
    print("-" * 74)
    for r in rows:
        short = (r["conversation_id"] or "")[:8]
        dt = ns_to_dt(r["first_ts"])
        agent_s = (r["agent"] or "?")[:16]
        model_s = (r["model"] or "?")[:18]
        print(f"{short}  {agent_s:<16}  {model_s:<18}  {dt:<13}  {r['user_turns']:>5}  {r['tool_calls']:>5}")


def show_conversation(
    conn: sqlite3.Connection,
    conv_id: str,
    *,
    compact: bool = False,
    tool_filter: str | None = None,
    full: bool = False,
    grep: re.Pattern | None = None,
) -> None:
    spans = conn.execute(
        "SELECT span_id, parent_span_id, name, start_time_unix_nano, attributes_json "
        "FROM spans WHERE conversation_id = ? ORDER BY start_time_unix_nano",
        (conv_id,),
    ).fetchall()

    if not spans:
        sys.exit(f"ERROR: conversation not found: {conv_id}")

    output_limit = None if full else TOOL_OUTPUT_LIMIT
    arg_limit = None if full else TOOL_INPUT_ARG_LIMIT

    meta_printed = False

    for row in spans:
        name = row["name"]
        ts = ns_to_time(row["start_time_unix_nano"])
        attrs = json.loads(row["attributes_json"]) if row["attributes_json"] else {}

        # --- conversation root ---
        if name == "conversation" and not meta_printed:
            short = conv_id[:8]
            print(f"{BOLD}conversation: {short}{RESET}  {DIM}({conv_id}){RESET}")
            agent = attrs.get("yuu.agent", "?")
            model = attrs.get("yuu.conversation.model", "?")
            print(f"agent: {CYAN}{agent}{RESET}  model: {CYAN}{model}{RESET}")
            print()
            meta_printed = True

        # --- turn (user or assistant) ---
        elif name == "turn":
            role = attrs.get("yuu.turn.role", "?")
            raw_items = attrs.get("yuu.turn.items")
            if not raw_items:
                continue
            items = json.loads(raw_items)

            if role == "user":
                # In compact mode with tool_filter, skip user turns
                if tool_filter:
                    continue
                print(f"{BOLD}[{ts}] {GREEN}USER{RESET}")
                for item in items:
                    if item.get("type") == "text":
                        text = item.get("text", "").strip()
                        if text:
                            print(highlight(text, grep))
                print()

            elif role == "assistant":
                has_text = False
                tool_call_count = 0
                tool_call_names: list[str] = []

                for item in items:
                    if item.get("type") == "text" and item.get("text", "").strip():
                        # In tool_filter mode, skip assistant text
                        if not tool_filter:
                            if not has_text:
                                print(f"{BOLD}[{ts}] {YELLOW}ASSISTANT{RESET}")
                                has_text = True
                            print(highlight(item["text"].strip(), grep))
                    elif item.get("type") == "tool_calls":
                        for tc in item.get("tool_calls", []):
                            fn = tc.get("name", tc.get("function", "?"))
                            tool_call_count += 1
                            tool_call_names.append(fn)
                            if not compact and not tool_filter:
                                if not has_text:
                                    print(f"{BOLD}[{ts}] {YELLOW}ASSISTANT{RESET}")
                                    has_text = True
                                args_str = fmt_tool_args(
                                    tc.get("arguments", "{}"),
                                    limit=arg_limit or TOOL_INPUT_ARG_LIMIT,
                                )
                                print(f"  {DIM}→ {fn}({args_str}){RESET}")

                if compact and tool_call_count and not tool_filter:
                    if not has_text:
                        print(f"{BOLD}[{ts}] {YELLOW}ASSISTANT{RESET}")
                        has_text = True
                    names = ", ".join(tool_call_names)
                    print(f"  {DIM}({tool_call_count} tool calls: {names}){RESET}")

                if has_text:
                    print()

        # --- tool span ---
        elif name.startswith("tool:"):
            tool_name = attrs.get("yuu.tool.name", name[5:])

            # Apply tool filter
            if tool_filter and tool_filter.lower() not in tool_name.lower():
                raw_input = attrs.get("yuu.tool.input", "{}")
                if tool_filter.lower() not in raw_input.lower():
                    continue

            if compact:
                continue

            raw_input = attrs.get("yuu.tool.input", "{}")
            raw_output = attrs.get("yuu.tool.output", "")

            lim = arg_limit or 99999
            tool_input = fmt_tool_args(raw_input, limit=lim)
            tool_output = decode_tool_output(raw_output)

            print(f"{BOLD}[{ts}] {MAGENTA}TOOL: {tool_name}{RESET}({tool_input})")
            if tool_output:
                if output_limit and len(tool_output) > output_limit:
                    tool_output = tool_output[:output_limit] + f"... [{len(tool_output)} chars]"
                for line in tool_output.splitlines():
                    print(f"  {highlight(line, grep)}")
            print()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        cast(_SupportsReconfigure, sys.stdout).reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Inspect yuuagents conversation traces")
    parser.add_argument("conv_id", nargs="?", help="conversation ID or prefix")
    parser.add_argument("-l", "--last", action="store_true", help="show most recent conversation")
    parser.add_argument("-n", "--compact", action="store_true", help="collapse tool calls (no tool output)")
    parser.add_argument("--tool", help="filter: only show tool calls matching this string")
    parser.add_argument("--full", action="store_true", help="no truncation on tool input/output")
    parser.add_argument("--grep", help="highlight/filter lines matching this pattern")
    parser.add_argument("--agent", help="filter list by agent name")
    parser.add_argument("--ctx", type=int, help="filter conversations by ctx_id suffix in runtime agent name")
    parser.add_argument("--limit", type=int, default=20, help="max conversations to list (default 20)")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--db", default=str(TRACES_DB), help="path to traces.db")
    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        _no_color()

    grep_pattern = re.compile(args.grep, re.IGNORECASE) if args.grep else None

    conn = open_db(Path(args.db))

    if args.last or args.conv_id:
        conv_id = (
            latest_id(conn, ctx_id=args.ctx)
            if args.last
            else resolve_id(conn, args.conv_id, ctx_id=args.ctx)
        )
        show_conversation(
            conn,
            conv_id,
            compact=args.compact,
            tool_filter=args.tool,
            full=args.full,
            grep=grep_pattern,
        )
    else:
        list_conversations(conn, agent=args.agent, limit=args.limit, ctx_id=args.ctx)


if __name__ == "__main__":
    main()
