"""Architecture-v2 CLI entrypoints."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import click

from yuubot.bootstrap.config import BootstrapConfig, load_bootstrap_config
from yuubot.bootstrap.layout import DataLayout
from yuubot.runtime.admin import build_admin
from yuubot.runtime.archive import ArchiveError, export_data, import_data
from yuubot.runtime.daemon import build_daemon
from yuubot.runtime.process import configure_file_logging


class DevProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


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
    config = load_bootstrap_config(ctx.obj["config_path"])
    layout = DataLayout.from_path(config.paths.data_dir)
    configure_file_logging(logs_dir=layout.logs_dir, process_name="daemon")

    async def run() -> None:
        app = await build_daemon(config)
        await app.serve()

    asyncio.run(run())


@cli.command("admin")
@click.pass_context
def admin(ctx: click.Context) -> None:
    """Start the Admin process."""
    config = load_bootstrap_config(ctx.obj["config_path"])
    layout = DataLayout.from_path(config.paths.data_dir)
    configure_file_logging(logs_dir=layout.logs_dir, process_name="admin")

    async def run() -> None:
        app = await build_admin(config)
        await app.serve()

    asyncio.run(run())


@cli.command("dev")
@click.pass_context
def dev(ctx: click.Context) -> None:
    """Start daemon and admin as two local child processes."""
    raise SystemExit(_run_dev(ctx.obj["config_path"]))


@dataclass(frozen=True)
class DevChild:
    name: str
    process: DevProcess
    health_url: str


DEV_SHUTDOWN_TIMEOUT_S = 5.0
DEV_FORCE_SIGNAL = signal.Signals(getattr(signal, "SIGKILL", signal.SIGTERM))
WEB_BUILD_INPUT_FILES = (
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "bun.lockb",
    "index.html",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.mts",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "components.json",
    ".env",
    ".env.local",
    ".env.production",
    ".env.production.local",
)
WEB_BUILD_INPUT_DIRS = ("src",)


def _run_dev(
    config_path: str | None,
    *,
    popen: Callable[[list[str]], DevProcess] | None = None,
    health_probe: Callable[[str], bool] | None = None,
    startup_timeout_s: float = 15.0,
    shutdown_timeout_s: float = DEV_SHUTDOWN_TIMEOUT_S,
    poll_interval_s: float = 0.1,
) -> int:
    """Run daemon/admin children and fail fast when startup health fails."""
    config = load_bootstrap_config(config_path)
    popen = popen or _popen_dev_child
    health_probe = health_probe or _health_probe

    _build_web(config)

    base = [sys.executable, "-m", "yuubot.cli"]
    config_args = ["--config", config_path] if config_path else []
    children = (
        DevChild(
            name="daemon",
            process=popen([*base, *config_args, "daemon"]),
            health_url=f"http://{config.server.daemon_host}:{config.server.daemon_port}/healthz",
        ),
        DevChild(
            name="admin",
            process=popen([*base, *config_args, "admin"]),
            health_url=f"http://{config.admin.host}:{config.admin.port}/healthz",
        ),
    )
    try:
        healthy = set[str]()
        deadline = time.monotonic() + startup_timeout_s
        while len(healthy) < len(children):
            for child in children:
                code = child.process.poll()
                if code is not None and child.name not in healthy:
                    click.echo(
                        f"{child.name} exited before startup completed with code {code}",
                        err=True,
                    )
                    return code if code else 1
                if child.name not in healthy and health_probe(child.health_url):
                    healthy.add(child.name)
            if time.monotonic() >= deadline:
                pending = ", ".join(
                    child.name for child in children if child.name not in healthy
                )
                click.echo(f"startup timed out waiting for: {pending}", err=True)
                return 1
            time.sleep(poll_interval_s)

        while True:
            for child in children:
                code = child.process.poll()
                if code is not None:
                    return code
            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        return 130
    finally:
        _shutdown_dev_children(children, timeout_s=shutdown_timeout_s)


def _popen_dev_child(argv: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        argv,
        start_new_session=os.name != "nt",
    )


def _shutdown_dev_children(children: tuple[DevChild, ...], *, timeout_s: float) -> None:
    running = tuple(child for child in children if child.process.poll() is None)
    for child in running:
        _send_process_signal(child.process, signal.SIGTERM)

    deadline = time.monotonic() + timeout_s
    stragglers = tuple(
        child for child in running if not _wait_until_exit(child, deadline)
    )
    for child in stragglers:
        click.echo(f"{child.name} did not exit after SIGTERM; killing", err=True)
        _send_process_signal(child.process, DEV_FORCE_SIGNAL)

    kill_deadline = time.monotonic() + timeout_s
    for child in stragglers:
        if not _wait_until_exit(child, kill_deadline):
            click.echo(f"{child.name} did not exit after SIGKILL", err=True)


def _wait_until_exit(child: DevChild, deadline: float) -> bool:
    if child.process.poll() is not None:
        return True
    timeout_s = max(0.0, deadline - time.monotonic())
    try:
        child.process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return child.process.poll() is not None
    return True


def _send_process_signal(process: DevProcess, sig: signal.Signals) -> None:
    if os.name != "nt":
        try:
            os.killpg(process.pid, sig)
            return
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    if sig == DEV_FORCE_SIGNAL:
        process.kill()
    else:
        process.terminate()


def _health_probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.2) as response:
            return 200 <= response.status < 500
    except OSError, urllib.error.URLError:
        return False


def _build_web(config: BootstrapConfig) -> None:
    """Build the admin frontend if the web project is present."""
    web_dist = Path(config.admin.web_dist_dir).resolve()
    web_root = web_dist.parent
    if not (web_root / "package.json").exists():
        return
    if _web_build_is_fresh(web_root, web_dist):
        click.echo(f"frontend build cache hit in {web_root}")
        return
    click.echo(f"building frontend in {web_root} ...")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(web_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        click.echo(result.stderr.strip(), err=True)
        raise click.ClickException("frontend build failed")


def _web_build_is_fresh(web_root: Path, web_dist: Path) -> bool:
    output_marker = web_dist / "index.html"
    if not output_marker.exists():
        return False
    return output_marker.stat().st_mtime >= _latest_mtime(
        _iter_web_build_inputs(web_root)
    )


def _iter_web_build_inputs(web_root: Path) -> Iterator[Path]:
    for name in WEB_BUILD_INPUT_FILES:
        path = web_root / name
        if path.is_file():
            yield path
    for name in WEB_BUILD_INPUT_DIRS:
        directory = web_root / name
        if directory.is_dir():
            yield from (path for path in directory.rglob("*") if path.is_file())


def _latest_mtime(paths: Iterable[Path]) -> float:
    latest = 0.0
    for path in paths:
        latest = max(latest, path.stat().st_mtime)
    return latest


@cli.group("trace")
@click.pass_context
def trace(ctx: click.Context) -> None:
    """Trace inspection commands."""


@trace.command("ui")
@click.option(
    "--host", "host", default=None, help="Override trace UI host (default: from config)"
)
@click.option(
    "--port",
    "port",
    default=None,
    type=int,
    help="Override trace UI port (default: from config)",
)
@click.pass_context
def trace_ui(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Launch the yuutrace Web UI to browse agent traces."""
    from yuutrace.cli.ui import run_ui

    config = load_bootstrap_config(ctx.obj["config_path"])
    layout = DataLayout.from_path(config.paths.data_dir)
    db_path = str(layout.traces_db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    ui_host = host or config.trace.ui_host
    ui_port = port or config.trace.ui_port

    click.echo(f"starting trace UI on http://{ui_host}:{ui_port}")
    click.echo(f"database: {db_path}")
    run_ui(db_path=db_path, host=ui_host, port=ui_port)


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
