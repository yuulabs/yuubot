"""CLI entry point for yuutrace.

Usage::

    ytrace server --db ./traces.db --port 4318
    ytrace ui --port 8080 --db ./traces.db
"""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytrace",
        description="yuutrace -- LLM observability CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ytrace server
    server_parser = sub.add_parser(
        "server",
        help="Start OTEL collector + SQLite storage backend",
    )
    server_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for the OTEL HTTP receiver (default: 127.0.0.1)",
    )
    server_parser.add_argument(
        "--db",
        default="./traces.db",
        help="Path to SQLite database file (default: ./traces.db)",
    )
    server_parser.add_argument(
        "--port",
        type=int,
        default=4318,
        help="Port for the OTEL HTTP receiver (default: 4318)",
    )

    # ytrace ui
    ui_parser = sub.add_parser(
        "ui",
        help="Start WebUI for trace visualization",
    )
    ui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for the WebUI HTTP server (default: 127.0.0.1)",
    )
    ui_parser.add_argument(
        "--db",
        default="./traces.db",
        help="Path to SQLite database file (default: ./traces.db)",
    )
    ui_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for the WebUI HTTP server (default: 8080)",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "server":
        from .server import run_server

        run_server(db_path=args.db, host=args.host, port=args.port)
    elif args.command == "ui":
        from .ui import run_ui

        run_ui(db_path=args.db, host=args.host, port=args.port)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
