"""Operator CLI: serve, deploy, check, migrate, and running-service control."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from ..app import DEFAULT_HOST, DEFAULT_PORT, Yuubot
from ..web.types import AppLoader
from . import commands


def main(argv: Sequence[str] | None = None, app_loader: AppLoader = Yuubot.from_config_file) -> int:
    try:
        return asyncio.run(_main_async(argv, app_loader))
    except KeyboardInterrupt:
        return 130


async def _main_async(argv: Sequence[str] | None, app_loader: AppLoader) -> int:
    parser = argparse.ArgumentParser(prog="yuubot")
    subcommands = parser.add_subparsers(dest="command", required=True)
    chat = subcommands.add_parser("chat")
    chat.add_argument("config")
    chat.add_argument("actor")
    chat.add_argument("message")
    chat.add_argument("--conversation-id", default=None)
    web = subcommands.add_parser("serve")
    web.add_argument("config")
    web.add_argument("--host", default=DEFAULT_HOST)
    web.add_argument("--port", type=int, default=DEFAULT_PORT)
    dev = subcommands.add_parser("dev")
    dev.add_argument("config")
    dev.add_argument("--host", default=DEFAULT_HOST)
    dev.add_argument("--port", type=int, default=DEFAULT_PORT)
    dev.add_argument("--web-host", default=DEFAULT_HOST)
    dev.add_argument("--web-port", type=int, default=5173)
    deploy = subcommands.add_parser("deploy")
    deploy.add_argument("config")
    deploy.add_argument("--dry-run", action="store_true")
    check = subcommands.add_parser("check")
    check.add_argument("config")
    check.add_argument("--json", action="store_true")
    migrate = subcommands.add_parser("migrate")
    migrate.add_argument("config")
    migrate.add_argument("--legacy-db", default=None)
    migrate.add_argument("--from-old-config", default=None)
    migrate.add_argument("--force-import", action="store_true")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--json", action="store_true")
    status = subcommands.add_parser("status")
    status.add_argument("config")
    status.add_argument("--json", action="store_true")
    interrupt = subcommands.add_parser("interrupt")
    interrupt.add_argument("config")
    interrupt_group = interrupt.add_mutually_exclusive_group(required=True)
    interrupt_group.add_argument("--conversation-id")
    interrupt_group.add_argument("--all", action="store_true")
    interrupt.add_argument("--json", action="store_true")
    stop = subcommands.add_parser("stop")
    stop.add_argument("config")
    stop.add_argument("--json", action="store_true")
    db = subcommands.add_parser("db")
    db_subcommands = db.add_subparsers(dest="db_command", required=True)
    db_info = db_subcommands.add_parser("info")
    db_info.add_argument("config")
    db_info.add_argument("--json", action="store_true")
    subcommands.add_parser("version")
    args = parser.parse_args(argv)

    if args.command == "chat":
        await commands.chat(app_loader, Path(args.config), str(args.actor), str(args.message), args.conversation_id)
        return 0
    if args.command == "serve":
        from ..web.server import serve_async

        await serve_async(Path(args.config), host=str(args.host), port=int(args.port), app_loader=app_loader)
        return 0
    if args.command == "dev":
        return await commands.dev(
            app_loader,
            Path(args.config),
            host=str(args.host),
            port=int(args.port),
            web_host=str(args.web_host),
            web_port=int(args.web_port),
        )
    if args.command == "deploy":
        return await commands.deploy(app_loader, Path(args.config), dry_run=bool(args.dry_run), json_output=False)
    if args.command == "check":
        return await commands.check(app_loader, Path(args.config), json_output=bool(args.json))
    if args.command == "migrate":
        return await commands.migrate_command(
            app_loader,
            Path(args.config),
            legacy_db=Path(args.legacy_db) if args.legacy_db else None,
            old_config=Path(args.from_old_config) if args.from_old_config else None,
            force_import=bool(args.force_import),
            dry_run=bool(args.dry_run),
            json_output=bool(args.json),
        )
    if args.command == "status":
        return commands.status(Path(args.config), json_output=bool(args.json))
    if args.command == "interrupt":
        return commands.interrupt(
            Path(args.config),
            conversation_id=args.conversation_id,
            interrupt_all=bool(args.all),
            json_output=bool(args.json),
        )
    if args.command == "stop":
        return commands.stop(Path(args.config), json_output=bool(args.json))
    if args.command == "db" and args.db_command == "info":
        return await commands.db_info(Path(args.config), json_output=bool(args.json))
    if args.command == "version":
        print(commands.version())
        return 0
    return 2


# Tests import these names from yuubot.cli.
_main_async = _main_async

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
