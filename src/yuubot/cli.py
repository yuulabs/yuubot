"""Click CLI main entry point for ybot."""

import asyncio
import os
import shlex
import shutil
import subprocess

import click


def _in_bot() -> bool:
    """Return True when CLI is invoked by the bot agent."""
    return os.environ.get("YUU_IN_BOT", "").lower() in ("1", "true", "yes")


class BotAwareGroup(click.Group):
    """A Click Group that hides certain commands when YUU_IN_BOT is set."""

    def __init__(self, *args, hidden_in_bot=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hidden_in_bot = hidden_in_bot or set()

    def list_commands(self, ctx: click.Context) -> list[str]:
        commands = super().list_commands(ctx)
        if _in_bot():
            commands = [c for c in commands if c not in self.hidden_in_bot]
        return commands

RECORDER_SESSION = "recorder"


def _screen_exists(name: str) -> bool:
    r = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
    return name in r.stdout


def _screen_quit(name: str) -> None:
    subprocess.run(["screen", "-S", name, "-X", "quit"], check=False)


async def _run_capability_cli(
    cap_name: str,
    action_name: str,
    argv: tuple[str, ...],
    config_path: str | None,
) -> None:
    """Run one capability action as a human-operated CLI command."""
    from yuubot.capabilities import CapabilityContext, execute
    from yuubot.capabilities.contract import load_all_contracts
    from yuubot.config import load_config
    from yuubot.core.db import close_db, init_db

    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        command_argv = argv
        contract = load_all_contracts().get(cap_name)
        action = None if contract is None else next(
            (item for item in contract.actions if item.name == action_name),
            None,
        )
        expects_payload = action is not None and action.payload_rule != "none"
        payload = ""
        if expects_payload:
            if not argv:
                raise click.ClickException(
                    f"{cap_name} {action_name} requires JSON payload after '--'"
                )
            payload = argv[-1].strip()
            if not payload or payload[0] not in "[{":
                raise click.ClickException(
                    f"{cap_name} {action_name} requires JSON payload after '--'"
                )
            command_argv = argv[:-1]

        command = shlex.join((cap_name, action_name, *command_argv))
        if payload:
            command = f"{command} -- {payload}"

        result = await execute(
            command,
            context=CapabilityContext(config=cfg),
        )
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                click.echo(block.get("text", ""))
            else:
                click.echo(str(block))
    finally:
        await close_db()


def _make_cap_action_command(cap_name: str, action_name: str, help_text: str) -> click.Command:
    @click.command(
        name=action_name,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        help=help_text,
    )
    @click.pass_context
    def _command(ctx: click.Context) -> None:
        asyncio.run(
            _run_capability_cli(
                cap_name,
                action_name,
                tuple(ctx.args),
                ctx.obj["config_path"],
            )
        )

    return _command


def _register_capability_commands(group: click.Group, cap_name: str) -> None:
    from yuubot.capabilities.contract import load_all_contracts

    contract = load_all_contracts().get(cap_name)
    if contract is None:
        return

    for action in contract.actions:
        if action.name in group.commands:
            continue
        group.add_command(
            _make_cap_action_command(cap_name, action.name, action.summary),
        )


@click.group(
    cls=BotAwareGroup,
    hidden_in_bot={"setup", "launch", "shutdown", "up", "down", "_recorder"},
)
@click.option("--config", "-c", default=None, help="Path to config.yaml")
@click.pass_context
def cli(ctx: click.Context, config: str | None) -> None:
    """yuubot — yuuagents enhanced QQ bot."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config

    # In-bot subprocess: suppress all console logging so tool output is clean.
    # Logs still go to log files via the daemon process.
    if _in_bot():
        from loguru import logger
        logger.remove()


# ── Phase 0: Setup ──────────────────────────────────────────────


@cli.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Interactive first-time setup: install NapCat, generate config, guide login."""
    from yuubot.setup import run_setup

    run_setup(ctx.obj["config_path"])


# ── Phase 1: Recorder ────────────────────────────────────────────


@cli.command()
@click.pass_context
def launch(ctx: click.Context) -> None:
    """Start NapCat + Recorder (both in background screen sessions)."""
    from urllib.parse import urlparse

    from yuubot import napcat
    from yuubot.config import load_config

    if not napcat.is_installed():
        click.echo("NapCat 未安装，请先运行 `ybot setup`。")
        raise SystemExit(1)

    # Ensure NapCat config matches config.yaml before starting
    cfg = load_config(ctx.obj["config_path"])
    http_port = urlparse(cfg.recorder.napcat_http).port or 3000
    napcat.write_onebot_config(
        cfg.bot.qq,
        ws_port=cfg.recorder.napcat_ws.port,
        http_port=http_port,
    )
    napcat.write_webui_port(cfg.recorder.napcat_webui_port)

    if not napcat.is_running():
        click.echo("启动 NapCat...")
        napcat.start()
        if not napcat.wait_ready():
            click.echo("NapCat 启动失败，请检查 screen -r napcat 查看日志。")
            raise SystemExit(1)
        click.echo("NapCat 已启动。")
    else:
        click.echo("NapCat 已在运行中。")

    if _screen_exists(RECORDER_SESSION):
        click.echo("Recorder 已在运行中。")
    else:
        click.echo("启动 Recorder...")
        cfg_arg = f" -c {ctx.obj['config_path']}" if ctx.obj["config_path"] else ""
        ybot_bin = shutil.which("ybot") or "ybot"
        subprocess.run(
            ["screen", "-dmS", RECORDER_SESSION, "bash", "-c",
             f"{ybot_bin}{cfg_arg} _recorder"],
            check=True,
        )
        import time
        time.sleep(2)
        if _screen_exists(RECORDER_SESSION):
            click.echo("Recorder 已启动。")
        else:
            click.echo("Recorder 启动失败，请检查 screen -r recorder 查看日志。")
            raise SystemExit(1)

    click.echo()
    click.echo("查看日志:")
    click.echo("  screen -r napcat      # NapCat 日志")
    click.echo("  screen -r recorder    # Recorder 日志")


@cli.command("_recorder", hidden=True)
@click.pass_context
def _recorder(ctx: click.Context) -> None:
    """Internal: run Recorder in foreground (called by screen)."""
    from yuubot.recorder.server import run_recorder

    asyncio.run(run_recorder(ctx.obj["config_path"]))


@cli.command()
@click.option("--recorder-only", is_flag=True, default=False, help="只关闭 Recorder，不关闭 NapCat")
@click.pass_context
def shutdown(ctx: click.Context, recorder_only: bool) -> None:
    """Stop Recorder + NapCat."""
    import httpx
    from yuubot.config import load_config

    cfg = load_config(ctx.obj["config_path"])
    api = f"http://{cfg.recorder.api.host}:{cfg.recorder.api.port}"

    # 1. Shutdown recorder
    try:
        r = httpx.post(f"{api}/shutdown", timeout=5)
        click.echo(f"Recorder 已关闭 ({r.status_code})")
    except httpx.ConnectError:
        click.echo("Recorder 未在运行。")
    # Clean up recorder screen session
    if _screen_exists(RECORDER_SESSION):
        import time
        time.sleep(1)
        _screen_quit(RECORDER_SESSION)

    if recorder_only:
        return

    # 2. Shutdown napcat
    from yuubot import napcat
    if napcat.is_running():
        napcat.stop()
        click.echo("NapCat 已关闭。")
    else:
        click.echo("NapCat 未在运行。")


# ── Phase 3: Daemon ──────────────────────────────────────────────


@cli.command()
@click.pass_context
def up(ctx: click.Context) -> None:
    """Start yuubot daemon."""
    from yuubot.daemon.app import run_daemon

    asyncio.run(run_daemon(ctx.obj["config_path"]))


@cli.command()
@click.pass_context
def down(ctx: click.Context) -> None:
    """Stop yuubot daemon."""
    import httpx
    from yuubot.config import load_config

    cfg = load_config(ctx.obj["config_path"])
    api = f"http://{cfg.daemon.api.host}:{cfg.daemon.api.port}"
    try:
        r = httpx.post(f"{api}/shutdown", timeout=5)
        click.echo(f"Daemon shutdown: {r.status_code}")
    except httpx.ConnectError:
        click.echo("Daemon not running.")


# ── Phase 4: Web Capability ──────────────────────────────────────


@cli.group(cls=BotAwareGroup, hidden_in_bot={"login"})
def web() -> None:
    """Web capability: search, read, download."""


@web.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Open browser for manual login (persist cookies)."""
    from yuubot.capabilities.web.reader import run_login

    run_login(ctx.obj["config_path"])


# ── Phase 5: IM Capability ───────────────────────────────────────


@cli.group(cls=BotAwareGroup, hidden_in_bot={"login"})
def im() -> None:
    """IM capability: send, search, list."""


@im.command("login")
@click.argument("im_name", default="qq")
@click.pass_context
def im_login(ctx: click.Context, im_name: str) -> None:
    """Login to IM (e.g. scan QR for QQ via NapCat)."""
    from yuubot import napcat

    if not napcat.is_installed():
        click.echo("NapCat 未安装，请先运行 `ybot setup`。")
        raise SystemExit(1)
    url = napcat.webui_url()
    token = napcat.webui_token()
    click.echo(f"请在浏览器中打开 NapCat WebUI: {url}")
    if token:
        click.echo(f"登录 Token: {token}")
    else:
        click.echo("⚠ 未能读取 WebUI Token，请查看 screen -r napcat 日志获取。")
    click.echo("在 WebUI 中输入 Token 后扫码登录 QQ。")


# ── Phase 6: Memory Capability ───────────────────────────────────


@cli.group()
def mem() -> None:
    """Memory capability: save, recall, delete, show."""


# ── Phase 6b: Image Capability ─────────────────────────────────


@cli.group()
def img() -> None:
    """Image capability: save, search, delete, list."""


# ── Phase 7: hhsh Capability ────────────────────────────────────


@cli.group()
def hhsh() -> None:
    """hhsh capability: 能不能好好说话 — translate abbreviations."""


# ── Phase 7b: Schedule Capability ────────────────────────────────


@cli.group()
def schedule() -> None:
    """Schedule capability: create, list, update, delete cron tasks."""


@cli.group()
def vision() -> None:
    """Vision capability: describe images."""


# ── Docker ───────────────────────────────────────────────────────


@cli.group(cls=BotAwareGroup, hidden_in_bot={"shell"})
def docker() -> None:
    """Docker container management."""


@docker.command("shell")
@click.option("--container", "-c", default="yagents-default",
              help="Container name to exec into.")
@click.pass_context
def docker_shell(ctx: click.Context, container: str) -> None:
    """Open an interactive shell in the running yuuagents container."""
    import subprocess

    # Check if container exists
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", container],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"Container '{container}' not found. "
            "Start the daemon first with 'ybot up'."
        )
    status = result.stdout.strip()
    if status != "running":
        click.echo(f"Container is {status}, starting it...")
        subprocess.run(["docker", "start", container], check=True)

    cmd = ["docker", "exec", "-it", "-u", "root", container, "/bin/bash"]
    click.echo(f"Attaching to {container}...")
    os.execvp("docker", cmd)


for _group, _cap_name in (
    (web, "web"),
    (im, "im"),
    (mem, "mem"),
    (img, "img"),
    (hhsh, "hhsh"),
    (schedule, "schedule"),
    (vision, "vision"),
):
    _register_capability_commands(_group, _cap_name)
