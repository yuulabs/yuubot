"""Click CLI main entry point for ybot."""

import asyncio
import os
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


@click.group(
    cls=BotAwareGroup,
    hidden_in_bot={"setup", "launch", "shutdown", "up", "down", "skills", "_recorder"},
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


# ── Phase 4: Web Skill ───────────────────────────────────────────


@cli.group(cls=BotAwareGroup, hidden_in_bot={"login"})
def web() -> None:
    """Web skill: search, read, download."""


@web.command()
@click.argument("query")
@click.option("--limit", default=5, help="Max results")
@click.pass_context
def search(ctx: click.Context, query: str, limit: int) -> None:
    """Search the web via Tavily."""
    from yuubot.skills.web.search import tavily_search

    asyncio.run(tavily_search(query, limit, ctx.obj["config_path"]))


@web.command()
@click.argument("url")
@click.option("--summary", is_flag=True, help="Only return summary")
@click.pass_context
def read(ctx: click.Context, url: str, summary: bool) -> None:
    """Read a web page and extract content."""
    from yuubot.skills.web.reader import read_url

    asyncio.run(read_url(url, summary, ctx.obj["config_path"]))


@web.command()
@click.argument("urls")
@click.argument("folder")
@click.pass_context
def download(ctx: click.Context, urls: str, folder: str) -> None:
    """Download files from URLs."""
    from yuubot.skills.web.downloader import download_urls

    asyncio.run(download_urls(urls, folder, ctx.obj["config_path"]))


@web.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Open browser for manual login (persist cookies)."""
    from yuubot.skills.web.reader import run_login

    run_login(ctx.obj["config_path"])


# ── Phase 5: IM Skill ────────────────────────────────────────────


@cli.group(cls=BotAwareGroup, hidden_in_bot={"login"})
def im() -> None:
    """IM skill: send, search, list."""


@im.command("send")
@click.option("--ctx", "ctx_id", type=int, default=None)
@click.option("--uid", type=int, default=None)
@click.option("--gid", type=int, default=None)
@click.option("--delay", type=float, default=0, help="Seconds to wait before sending.")
@click.pass_context
def im_send(ctx: click.Context, ctx_id: int | None, uid: int | None, gid: int | None, delay: float) -> None:
    """Send a message (reads JSON from stdin)."""
    import sys

    msg = sys.stdin.read()
    if not msg.strip():
        click.echo("错误: 消息内容为空")
        raise SystemExit(1)

    from yuubot.skills.im.cli import send_msg

    asyncio.run(send_msg(msg, ctx_id, uid, gid, ctx.obj["config_path"], delay=delay))


@im.command("search")
@click.argument("keywords")
@click.option("--ctx", "ctx_id", type=int, default=None)
@click.option("--limit", default=20)
@click.option("--days", default=7)
@click.pass_context
def im_search(ctx: click.Context, keywords: str, ctx_id: int | None, limit: int, days: int) -> None:
    """Search messages (outputs LLM-readable XML format)."""
    from yuubot.skills.im.cli import search_msg

    asyncio.run(search_msg(keywords, ctx_id, limit, days, ctx.obj["config_path"]))


@im.command("browse")
@click.option("--msg", "msg_id", type=int, default=None, help="Center message ID")
@click.option("--ctx", "ctx_id", type=int, default=None, help="Filter by context ID")
@click.option("--before", default=10, help="Messages before center")
@click.option("--after", default=10, help="Messages after center")
@click.option("--since", default=None, help="Start time (ISO format)")
@click.option("--until", default=None, help="End time (ISO format)")
@click.option("--limit", default=50, help="Max messages")
@click.pass_context
def im_browse(
    ctx: click.Context,
    msg_id: int | None,
    ctx_id: int | None,
    before: int,
    after: int,
    since: str | None,
    until: str | None,
    limit: int,
) -> None:
    """Browse messages (outputs LLM-readable XML format)."""
    from yuubot.skills.im.cli import browse_msg

    asyncio.run(browse_msg(msg_id, ctx_id, before, after, since, until, limit, ctx.obj["config_path"]))


@im.command("list")
@click.argument("target", type=click.Choice(["friends", "groups", "members", "contexts"]))
@click.option("--gid", type=int, default=None)
@click.pass_context
def im_list(ctx: click.Context, target: str, gid: int | None) -> None:
    """List friends/groups/members/contexts."""
    from yuubot.skills.im.cli import list_info

    asyncio.run(list_info(target, gid, ctx.obj["config_path"]))


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


# ── Phase 6: Memory Skill ────────────────────────────────────────


@cli.group()
def mem() -> None:
    """Memory skill: save, recall, delete, show."""


@mem.command("save")
@click.argument("content")
@click.option("--tags", default="")
@click.option("--scope", type=click.Choice(["private", "public"]), default="private")
@click.pass_context
def mem_save(ctx: click.Context, content: str, tags: str, scope: str) -> None:
    """Save a memory."""
    from yuubot.skills.mem.cli import save_memory

    asyncio.run(save_memory(content, tags, scope, ctx.obj["config_path"]))


@mem.command("recall")
@click.argument("words")
@click.option("--tags", default="")
@click.option("--limit", default=10)
@click.pass_context
def mem_recall(ctx: click.Context, words: str, tags: str, limit: int) -> None:
    """Recall memories by keywords/tags."""
    from yuubot.skills.mem.cli import recall_memory

    asyncio.run(recall_memory(words, tags, limit, ctx.obj["config_path"]))


@mem.command("delete")
@click.argument("ids")
@click.pass_context
def mem_delete(ctx: click.Context, ids: str) -> None:
    """Delete memories by IDs (comma-separated)."""
    from yuubot.skills.mem.cli import delete_memory

    asyncio.run(delete_memory(ids, ctx.obj["config_path"]))


@mem.command("show")
@click.option("--tags", is_flag=True, help="Show all tags")
@click.pass_context
def mem_show(ctx: click.Context, tags: bool) -> None:
    """Show memory tags."""
    from yuubot.skills.mem.cli import show_tags

    asyncio.run(show_tags(ctx.obj["config_path"]))


@mem.command("config")
@click.option("--forget-days", type=int, default=None)
@click.pass_context
def mem_config(ctx: click.Context, forget_days: int | None) -> None:
    """Configure memory system."""
    from yuubot.skills.mem.cli import configure_memory

    asyncio.run(configure_memory(forget_days, ctx.obj["config_path"]))


# ── Phase 6b: Image Skill ──────────────────────────────────────


@cli.group()
def img() -> None:
    """Image skill: save, search, delete, list."""


@img.command("save")
@click.argument("path")
@click.option("--desc", default="", help="Image description")
@click.option("--tags", default="", help="Comma-separated tags")
@click.pass_context
def img_save(ctx: click.Context, path: str, desc: str, tags: str) -> None:
    """Save an image to the library."""
    from yuubot.skills.img.cli import save_image

    asyncio.run(save_image(path, desc, tags, ctx.obj["config_path"]))


@img.command("search")
@click.argument("query", default="")
@click.option("--tags", default="", help="Comma-separated tags to filter")
@click.option("--limit", default=10, help="Max results")
@click.pass_context
def img_search(ctx: click.Context, query: str, tags: str, limit: int) -> None:
    """Search images by description/tags."""
    from yuubot.skills.img.cli import search_image

    asyncio.run(search_image(query, tags, limit, ctx.obj["config_path"]))


@img.command("delete")
@click.argument("image_id", type=int)
@click.pass_context
def img_delete(ctx: click.Context, image_id: int) -> None:
    """Delete an image by ID."""
    from yuubot.skills.img.cli import delete_image

    asyncio.run(delete_image(image_id, ctx.obj["config_path"]))


@img.command("list")
@click.option("--tags", "show_tags", is_flag=True, help="Show all tags")
@click.option("--limit", default=20, help="Max images to show")
@click.pass_context
def img_list(ctx: click.Context, show_tags: bool, limit: int) -> None:
    """List images or tags."""
    from yuubot.skills.img.cli import list_images

    asyncio.run(list_images(show_tags, limit, ctx.obj["config_path"]))


# ── Phase 7: hhsh Skill ─────────────────────────────────────────


@cli.group()
def hhsh() -> None:
    """hhsh skill: 能不能好好说话 — translate abbreviations."""


@hhsh.command("guess")
@click.argument("text")
@click.pass_context
def hhsh_guess(ctx: click.Context, text: str) -> None:
    """Translate abbreviation via nbnhhsh."""
    from yuubot.skills.hhsh.cli import run_guess

    asyncio.run(run_guess(text))


# ── Phase 7b: Schedule Skill ─────────────────────────────────────


@cli.group()
def schedule() -> None:
    """Schedule skill: create, list, update, delete cron tasks."""


@schedule.command("create")
@click.argument("cron_expr")
@click.argument("task")
@click.option("--agent", default=None, help="Agent to run the task (default: caller's own name)")
@click.option("--ctx", "ctx_id", type=int, default=None, help="Target context ID")
@click.option("--recurring", is_flag=True, default=False, help="Repeat on schedule (default is once)")
@click.pass_context
def schedule_create(ctx: click.Context, cron_expr: str, task: str, agent: str | None, ctx_id: int | None, recurring: bool) -> None:
    """Create a scheduled task with cron expression."""
    from yuubot.skills.schedule.cli import create_task

    asyncio.run(create_task(cron_expr, task, agent, ctx_id, once=not recurring, config_path=ctx.obj["config_path"]))


@schedule.command("list")
@click.option("--all", "show_all", is_flag=True, default=False, help="Show all tasks including disabled")
@click.pass_context
def schedule_list(ctx: click.Context, show_all: bool) -> None:
    """List scheduled tasks (active only by default)."""
    from yuubot.skills.schedule.cli import list_tasks

    asyncio.run(list_tasks(ctx.obj["config_path"], show_all=show_all))


@schedule.command("update")
@click.argument("task_id", type=int)
@click.option("--cron", "cron_expr", default=None, help="New cron expression")
@click.option("--task", "task_text", default=None, help="New task description")
@click.option("--agent", default=None, help="New agent name")
@click.option("--enable/--disable", default=None, help="Enable or disable the task")
@click.pass_context
def schedule_update(ctx: click.Context, task_id: int, cron_expr: str | None, task_text: str | None, agent: str | None, enable: bool | None) -> None:
    """Update a scheduled task."""
    from yuubot.skills.schedule.cli import update_task

    asyncio.run(update_task(task_id, cron_expr, task_text, agent, enable, ctx.obj["config_path"]))


@schedule.command("delete")
@click.argument("task_id", type=int)
@click.pass_context
def schedule_delete(ctx: click.Context, task_id: int) -> None:
    """Delete a scheduled task."""
    from yuubot.skills.schedule.cli import delete_task

    asyncio.run(delete_task(task_id, ctx.obj["config_path"]))


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

    cmd = ["docker", "exec", "-it", container, "/bin/bash"]
    click.echo(f"Attaching to {container}...")
    os.execvp("docker", cmd)


# ── Phase 8: Skills Install ──────────────────────────────────────


@cli.group()
def skills() -> None:
    """Manage skills."""


@skills.command("install")
@click.argument("skill_name", required=False, default=None)
@click.pass_context
def skills_install(ctx: click.Context, skill_name: str | None) -> None:
    """Install skill SKILL.md to yuuagents. No argument = install all."""
    from yuubot.skills.install import install_skill

    install_skill(skill_name, ctx.obj["config_path"])
