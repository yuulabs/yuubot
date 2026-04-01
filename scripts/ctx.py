#!/usr/bin/env python3
"""Inspect real QQ-side messages from yuubot's message DB.

Examples:
    python scripts/ctx.py --ctx 2
    python scripts/ctx.py --ctx 2 --after-msg 1533390347 --limit 50
    python scripts/ctx.py --msg 1533390347 --before 10 --after 20
    python scripts/ctx.py --ctx 2 --qq 1216328129
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from yuubot.capabilities.im.formatter import format_messages_to_xml
from yuubot.capabilities.im.query import (
    browse_messages,
    recent_messages,
    resolve_message_db_id,
)
from yuubot.config import load_config
from yuubot.core.db import close_db, init_db
from yuubot.daemon.bot_info import BotInfo


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Inspect real QQ transcript from messages DB")
    p.add_argument("--config", "-c", default=None, help="Path to config.yaml")
    p.add_argument("--db", default=None, help="Override yuubot.db path")
    p.add_argument("--ctx", type=int, default=None, help="Context ID")
    p.add_argument("--msg", type=int, default=None, help="Center message ID for browse mode")
    p.add_argument("--after-msg", type=int, default=None, help="Show messages newer than this message ID")
    p.add_argument("--before", type=int, default=10, help="Browse mode: messages before center")
    p.add_argument("--after", type=int, default=10, help="Browse mode: messages after center")
    p.add_argument("--since", default=None, help="Browse mode: ISO start time")
    p.add_argument("--until", default=None, help="Browse mode: ISO end time")
    p.add_argument("--qq", default=None, help="Filter browse mode by QQ ids (comma-separated)")
    p.add_argument("--name", default=None, help="Filter browse mode by nickname/display name")
    p.add_argument("--limit", type=int, default=50, help="Max messages")
    return p


def _parse_qq_ids(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    db_path = args.db or cfg.database.path
    await init_db(db_path, simple_ext=cfg.database.simple_ext)
    try:
        bot_info = BotInfo(cfg)
        bot_name = await bot_info.bot_name()

        if args.after_msg is not None:
            if args.ctx is None:
                raise SystemExit("--after-msg requires --ctx")
            after_row_id = await resolve_message_db_id(
                message_id=args.after_msg,
                ctx_id=args.ctx,
            )
            if after_row_id == 0:
                raise SystemExit(f"message {args.after_msg} not found in ctx {args.ctx}")
            messages = await recent_messages(
                args.ctx,
                after_row_id=after_row_id,
                limit=args.limit,
            )
        else:
            since = datetime.fromisoformat(args.since) if args.since else None
            until = datetime.fromisoformat(args.until) if args.until else None
            messages = await browse_messages(
                msg_id=args.msg,
                ctx_id=args.ctx,
                before=args.before,
                after=args.after,
                since=since,
                until=until,
                limit=args.limit,
                qq_ids=_parse_qq_ids(args.qq),
                name_pattern=args.name,
            )

        if not messages:
            print("未找到消息")
            return 0

        xml = await format_messages_to_xml(
            messages,
            bot_qq=cfg.bot.qq,
            bot_name=bot_name,
        )
        print(xml)
        return 0
    finally:
        await close_db()


def main() -> int:
    return asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
