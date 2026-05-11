"""Architecture-v2 CLI entrypoints."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import click

from yuubot.bootstrap.config import load_bootstrap_config
from yuubot.runtime.admin import build_admin
from yuubot.runtime.daemon import build_daemon


@click.group()
@click.option("--config", "config_path", default=None, help="Path to v2 config.yaml")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """yuubot v2 core commands."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@cli.command("check")
@click.pass_context
def check(ctx: click.Context) -> None:
    """Validate bootstrap config only."""
    config = load_bootstrap_config(ctx.obj["config_path"])
    click.echo("bootstrap: ok")
    click.echo(f"database: {config.database.path}")
    click.echo(f"admin: {config.admin.host}:{config.admin.port}")
    click.echo(f"trace-ui: {config.trace.ui_host}:{config.trace.ui_port}")


@cli.command("daemon")
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Start the Yuubot daemon process."""

    async def run() -> None:
        app = await build_daemon(load_bootstrap_config(ctx.obj["config_path"]))
        await app.serve()

    asyncio.run(run())


@cli.command("admin")
@click.pass_context
def admin(ctx: click.Context) -> None:
    """Start the Admin process."""

    async def run() -> None:
        app = await build_admin(load_bootstrap_config(ctx.obj["config_path"]))
        await app.serve()

    asyncio.run(run())


@cli.command("dev")
@click.pass_context
def dev(ctx: click.Context) -> None:
    """Start daemon and admin as two local child processes."""

    base = [sys.executable, "-m", "yuubot.cli"]
    config_args = ["--config", ctx.obj["config_path"]] if ctx.obj["config_path"] else []
    daemon_proc = subprocess.Popen([*base, *config_args, "daemon"])
    admin_proc = subprocess.Popen([*base, *config_args, "admin"])
    try:
        first = None
        while first is None:
            for proc in (daemon_proc, admin_proc):
                code = proc.poll()
                if code is not None:
                    first = code
                    break
        raise SystemExit(first)
    finally:
        for proc in (daemon_proc, admin_proc):
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    cli()
