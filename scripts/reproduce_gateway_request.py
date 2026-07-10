"""Build an OpenAI-compatible curl request from a stored yuubot conversation."""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("conversation_id")
    parser.add_argument("--model", default="kimi-k2.7-code")
    parser.add_argument("--base-url", default="https://opencode.ai/zen/go/v1")
    parser.add_argument("--output", type=Path, default=Path("yuubot-repro-payload.json"))
    args = parser.parse_args()

    with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as db:
        rows = db.execute(
            "select kind, cast(payload as text) from history "
            "where conversation_id = ? order by seq",
            (args.conversation_id,),
        ).fetchall()
        actor_row = db.execute(
            "select actor_id from app_conversations where id = ?",
            (args.conversation_id,),
        ).fetchone()

    records = {kind: json.loads(payload) for kind, payload in rows}
    if "tool_specs" not in records or "system_prompt" not in records:
        raise SystemExit("conversation has no stored tool_specs/system_prompt prefix")

    messages: list[dict[str, object]] = [
        {"role": "system", "content": records["system_prompt"]["text"]}
    ]
    for kind, payload in ((kind, json.loads(raw)) for kind, raw in rows):
        if kind != "input":
            continue
        content_items = payload["content"]
        if len(content_items) == 1 and content_items[0]["kind"] == "text":
            content: object = content_items[0]["text"]
        else:
            content = [
                {
                    "type": "text" if item["kind"] == "text" else "image_url",
                    "text": item["text"]
                    if item["kind"] == "text"
                    else None,
                    "image_url": {"url": item["url"]}
                    if item["kind"] == "image"
                    else None,
                }
                for item in content_items
            ]
        messages.append({"role": "user" if payload["role"] == "user" else "system", "content": content})

    actor_id = str(actor_row[0]) if actor_row else ""
    body = {
        "model": args.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": records["tool_specs"]["specs"],
        "metadata": {
            "trace_id": args.conversation_id,
            "actor_id": actor_id,
            "conversation_id": args.conversation_id,
            "purpose": "chat",
        },
    }
    args.output.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Payload:", args.output)
    print("\nRun:")
    command = (
        f"curl -N -sS -D - --max-time 180 {shlex.quote(args.base_url.rstrip('/') + '/chat/completions')} "
        '-H "Authorization: Bearer $API_KEY" '
        '-H "Content-Type: application/json" '
        f"--data-binary @{shlex.quote(str(args.output))}"
    )
    print(command)


if __name__ == "__main__":
    main()
