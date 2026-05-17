"""Architecture-v2 CLI entrypoints."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import click

from yuubot.bootstrap.config import load_bootstrap_config
from yuubot.bootstrap.layout import DataLayout
from yuubot.runtime.admin import build_admin
from yuubot.runtime.archive import ArchiveError, export_data, import_data
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


@cli.command("export")
@click.argument("out_path", type=click.Path(dir_okay=False))
@click.pass_context
def export_command(ctx: click.Context, out_path: str) -> None:
    """Snapshot the data directory into a zip archive.

    The daemon and admin processes should be stopped before running this.
    """
    config = load_bootstrap_config(ctx.obj["config_path"])
    layout = DataLayout.from_path(config.paths.data_dir)
    if not layout.data_dir.is_dir():
        raise click.ClickException(f"data_dir {layout.data_dir} does not exist")
    archive = export_data(layout.data_dir, out_path)
    click.echo(f"wrote {archive}")


@cli.command("import")
@click.argument("in_path", type=click.Path(dir_okay=False, exists=True))
@click.option(
    "--replace",
    is_flag=True,
    help="Wipe the destination data_dir before extracting",
)
@click.pass_context
def import_command(ctx: click.Context, in_path: str, replace: bool) -> None:
    """Extract an archive into the configured data directory.

    The daemon and admin processes must be stopped before running this.
    """
    config = load_bootstrap_config(ctx.obj["config_path"])
    layout = DataLayout.from_path(config.paths.data_dir)
    layout.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        manifest = import_data(in_path, layout.data_dir, replace=replace)
    except ArchiveError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"imported manifest_version={manifest.manifest_version} "
        f"created_at={manifest.created_at} into {layout.data_dir}"
    )


if __name__ == "__main__":
    cli()
