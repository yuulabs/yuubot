"""Click CLI main entry point for ybot."""

import asyncio
import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import time

import click
import httpx


async def _with_db(config_path: str | None, action):
    from yuubot.config import load_config
    from yuubot.core.db import close_db, init_db

    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        return await action(cfg)
    finally:
        await close_db()


def _load_config(config_path: str | None):
    from yuubot.config import load_config

    return load_config(config_path)


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


def _screen_session_ids(name: str) -> list[str]:
    r = subprocess.run(["screen", "-ls"], capture_output=True, text=True, check=False)
    pattern = re.compile(rf"^\s*(\d+\.{re.escape(name)})\s", re.MULTILINE)
    return pattern.findall(r.stdout)


def _screen_exists(name: str) -> bool:
    return bool(_screen_session_ids(name))


def _daemon_api_alive(api: str) -> bool:
    try:
        response = httpx.get(f"{api.rstrip('/')}/health", timeout=2)
    except httpx.RequestError:
        return False
    return 200 <= response.status_code < 300


def _daemon_api_host_for_host_network(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def _daemon_api_url_for_host(cfg) -> str:
    host = _daemon_api_host_for_host_network(cfg.daemon.api.host)
    return f"http://{host}:{cfg.daemon.api.port}"


def _wait_daemon_api(api: str, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _daemon_api_alive(api):
            return True
        time.sleep(1)
    return _daemon_api_alive(api)


def _screen_quit(name: str) -> None:
    for session_id in _screen_session_ids(name):
        subprocess.run(["screen", "-S", session_id, "-X", "quit"], check=False)


@click.group(
    cls=BotAwareGroup,
    hidden_in_bot={"setup", "launch", "shutdown", "_recorder"},
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
        napcat.start(qq_direct=cfg.network.qq_direct)
        qr = napcat.capture_qrcode(timeout=30)
        if qr:
            click.echo()
            click.echo(qr)
            click.echo()
        elif not napcat.is_running():
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


@cli.command()
@click.option("--port", default=8080, show_default=True, help="WebUI 监听端口")
@click.option("--host", default="127.0.0.1", show_default=True, help="WebUI 监听地址")
@click.pass_context
def traces(ctx: click.Context, port: int, host: str) -> None:
    """启动 traces WebUI（查看 LLM 对话追踪）。"""
    from pathlib import Path
    from yuubot.config import load_config
    from yuutrace.cli.ui import run_ui

    cfg = load_config(ctx.obj["config_path"])
    # Check current yuuagents tracing config first, then legacy fallback.
    tracing_cfg = cfg.yuuagents.get("yuutrace") or cfg.yuuagents.get("tracing") or {}
    db_path = str(
        Path(tracing_cfg.get("db_path") or "~/.yagents/traces.db").expanduser()
    )
    click.echo(f"Traces DB: {db_path}")
    click.echo(f"WebUI: http://{host}:{port}")
    run_ui(db_path=db_path, host=host, port=port)


# ── Phase 2: Daemon ──────────────────────────────────────────────


@cli.command()
@click.pass_context
def up(ctx: click.Context) -> None:
    """Start the yuubot daemon with the RFC2 yuuagents skeleton."""
    from yuubot.daemon.app import run_daemon

    asyncio.run(run_daemon(ctx.obj["config_path"]))


@cli.command()
@click.pass_context
def down(ctx: click.Context) -> None:
    """Request graceful daemon shutdown."""
    from yuubot.config import load_config

    cfg = load_config(ctx.obj["config_path"])
    api = f"http://{cfg.daemon.api.host}:{cfg.daemon.api.port}"
    try:
        response = httpx.post(f"{api}/shutdown", timeout=5)
        click.echo(f"Daemon shutdown requested ({response.status_code})")
    except httpx.ConnectError:
        click.echo("Daemon 未在运行。")


@cli.command("export")
@click.argument("categories", nargs=-1)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="导出 zip 归档路径",
)
@click.pass_context
def export_archive_cmd(ctx: click.Context, categories: tuple[str, ...], output: Path) -> None:
    """Export standardized portability archive."""
    from yuubot.config import load_config
    from yuubot.portability import export_archive, parse_categories

    cfg = load_config(ctx.obj["config_path"])
    selected = parse_categories(list(categories))
    manifest = export_archive(cfg, output, selected)
    click.echo(f"Exported {output}")
    click.echo(f"Categories: {', '.join(manifest.categories)}")
    click.echo(f"Entries: {sum(len(entry.payload_paths) for entry in manifest.entries.values())}")


@cli.command("import")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("categories", nargs=-1)
@click.option("--dry-run", is_flag=True, default=False, help="只校验归档，不写入目标路径")
@click.pass_context
def import_archive_cmd(
    ctx: click.Context,
    archive: Path,
    categories: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Import standardized portability archive."""
    from yuubot.config import load_config
    from yuubot.portability import import_archive, parse_categories

    cfg = load_config(ctx.obj["config_path"])
    selected = parse_categories(list(categories)) if categories else None
    manifest = import_archive(cfg, archive, categories=selected, dry_run=dry_run)
    action = "Validated" if dry_run else "Imported"
    click.echo(f"{action} {archive}")
    click.echo(f"Categories: {', '.join(selected or tuple(manifest.categories))}")
    click.echo(f"Entries: {sum(len(entry.payload_paths) for entry in manifest.entries.values())}")


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


@im.command("send")
@click.option("--ctx", "ctx_id", type=int, default=None, help="目标 ctx_id")
@click.option("--uid", type=int, default=None, help="目标私聊 QQ")
@click.option("--gid", type=int, default=None, help="目标群号")
@click.argument("message", nargs=-1)
@click.pass_context
def im_send(ctx: click.Context, ctx_id: int | None, uid: int | None, gid: int | None, message: tuple[str, ...]) -> None:
    """Send a text or OneBot-segment JSON message."""

    text = " ".join(message).strip()

    async def _run(cfg):
        from yuubot.services.im import ImService

        payload = {
            "bot_kind": "master",
            "ctx_id": ctx_id or 0,
            "target_ctx_id": ctx_id,
            "target_user_id": uid,
            "target_group_id": gid,
            "recorder_base_url": cfg.daemon.recorder_api,
        }
        if text.startswith("["):
            payload["segments"] = json.loads(text)
        else:
            payload["text"] = text
        return await ImService(config=cfg).send_message(payload)

    result = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    click.echo(json.dumps(result, ensure_ascii=False))


@im.command("recent")
@click.option("--ctx", "ctx_id", type=int, required=True, help="ctx_id")
@click.option("--limit", type=int, default=30, show_default=True)
@click.pass_context
def im_recent(ctx: click.Context, ctx_id: int, limit: int) -> None:
    """List recent messages for a ctx."""

    async def _run(cfg):
        from yuubot.services.im import ImService

        return await ImService(config=cfg).recent_messages(
            {"bot_kind": "master", "ctx_id": ctx_id, "limit": limit}
        )

    rows = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    for row in rows:
        click.echo(f"[{row['message_id']}] ctx={row['ctx_id']} qq={row['user_id']} {row['content']}")


@im.command("search")
@click.option("--ctx", "ctx_id", type=int, default=None, help="限定 ctx_id")
@click.option("--limit", type=int, default=20, show_default=True)
@click.argument("query", nargs=-1, required=True)
@click.pass_context
def im_search(ctx: click.Context, ctx_id: int | None, limit: int, query: tuple[str, ...]) -> None:
    """Search stored messages."""

    async def _run(cfg):
        from yuubot.services.im import ImService

        return await ImService(config=cfg).search_messages(
            {
                "bot_kind": "master",
                "ctx_id": ctx_id or 0,
                "target_ctx_id": ctx_id,
                "query": " ".join(query),
                "limit": limit,
            }
        )

    rows = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    for row in rows:
        click.echo(f"[{row['message_id']}] ctx={row['ctx_id']} qq={row['user_id']} {row['content']}")


@cli.group()
def mem() -> None:
    """Memory service: save, recall, list, archive, restore."""


@mem.command("save")
@click.option("--ctx", "ctx_id", type=int, default=None, help="ctx_id for private memory")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--scope", default="private", show_default=True, help="private or public")
@click.argument("content", nargs=-1, required=True)
@click.pass_context
def mem_save(ctx: click.Context, ctx_id: int | None, tags: str, scope: str, content: tuple[str, ...]) -> None:
    """Save a memory."""

    async def _run(cfg):
        from yuubot.services.mem import MemoryService

        return await MemoryService(config=cfg).save(
            {
                "bot_kind": "master",
                    "ctx_id": ctx_id,
                "content": " ".join(content),
                "tags": [item.strip() for item in tags.split(",") if item.strip()],
                "scope": scope,
            }
        )

    result = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    click.echo(f"saved mem {result['id']}")


@mem.command("recall")
@click.option("--ctx", "ctx_id", type=int, default=None, help="ctx_id")
@click.option("--limit", type=int, default=10, show_default=True)
@click.argument("query", nargs=-1, required=True)
@click.pass_context
def mem_recall(ctx: click.Context, ctx_id: int | None, limit: int, query: tuple[str, ...]) -> None:
    """Recall memories."""

    async def _run(cfg):
        from yuubot.services.mem import MemoryService

        return await MemoryService(config=cfg).recall(
            {
                "bot_kind": "master",
                    "ctx_id": ctx_id,
                "query": " ".join(query),
                "limit": limit,
                "scope": "all" if ctx_id is None else "",
            }
        )

    rows = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    for row in rows:
        click.echo(f"[mem {row['id']}] tags={','.join(row['tags'])} {row['content']}")


@mem.command("list")
@click.option("--ctx", "ctx_id", type=int, default=None, help="ctx_id")
@click.option("--all", "show_all", is_flag=True, default=False)
@click.option("--trash", is_flag=True, default=False)
@click.option("--limit", type=int, default=50, show_default=True)
@click.pass_context
def mem_list(ctx: click.Context, ctx_id: int | None, show_all: bool, trash: bool, limit: int) -> None:
    """List memories."""

    async def _run(cfg):
        from yuubot.services.mem import MemoryService

        return await MemoryService(config=cfg).list(
            {
                "bot_kind": "master",
                    "ctx_id": ctx_id,
                "all": show_all,
                "trash": trash,
                "limit": limit,
            }
        )

    rows = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    for row in rows:
        click.echo(f"[mem {row['id']}] scope={row['scope']} ctx={row['ctx_id']} {row['content']}")


@mem.command("archive")
@click.argument("ids", nargs=-1, required=True)
@click.pass_context
def mem_archive(ctx: click.Context, ids: tuple[str, ...]) -> None:
    """Move memories to trash."""

    async def _run(cfg):
        from yuubot.services.mem import MemoryService

        return await MemoryService(config=cfg).archive(
            {"bot_kind": "master", "ids": [int(item) for item in ids]}
        )

    result = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    click.echo(f"archived {result['count']}")


@mem.command("restore")
@click.argument("ids", nargs=-1, required=True)
@click.pass_context
def mem_restore(ctx: click.Context, ids: tuple[str, ...]) -> None:
    """Restore trashed memories."""

    async def _run(cfg):
        from yuubot.services.mem import MemoryService

        return await MemoryService(config=cfg).restore(
            {"bot_kind": "master", "ids": [int(item) for item in ids]}
        )

    result = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    click.echo(f"restored {result['count']}")


@cli.group()
def web() -> None:
    """Web service: search, read, download."""


@web.command("search")
@click.option("--limit", type=int, default=5, show_default=True)
@click.argument("query", nargs=-1, required=True)
@click.pass_context
def web_search(ctx: click.Context, limit: int, query: tuple[str, ...]) -> None:
    """Search with the configured provider."""

    async def _run(cfg):
        from yuubot.services.web import WebService

        return await WebService(config=cfg).search({"query": " ".join(query), "limit": limit})

    rows = asyncio.run(_run(_load_config(ctx.obj["config_path"])))
    for idx, row in enumerate(rows, 1):
        click.echo(f"{idx}. [{row['title']}] {row['url']}")
        if row.get("content"):
            click.echo(f"   {row['content'][:200]}")


@web.command("read")
@click.argument("url")
@click.pass_context
def web_read(ctx: click.Context, url: str) -> None:
    """Read a page and print extracted text."""

    async def _run(cfg):
        from yuubot.services.web import WebService

        return await WebService(config=cfg).read_page({"url": url})

    result = asyncio.run(_run(_load_config(ctx.obj["config_path"])))
    click.echo(f"# {result['title']}\n- URL: {result['url']}\n\n---\n\n{result['text']}")


@web.command("download")
@click.argument("url")
@click.option("--filename", default="", help="Output filename")
@click.pass_context
def web_download(ctx: click.Context, url: str, filename: str) -> None:
    """Download a URL to the configured download directory."""

    async def _run(cfg):
        from yuubot.services.web import WebService

        return await WebService(config=cfg).download({"url": url, "filename": filename})

    result = asyncio.run(_run(_load_config(ctx.obj["config_path"])))
    click.echo(f"downloaded {result['bytes']} bytes -> {result['path']}")


@cli.group()
def schedule() -> None:
    """Schedule service: create, list, cancel."""


@schedule.command("create")
@click.argument("cron")
@click.argument("task", nargs=-1, required=True)
@click.option("--ctx", "ctx_id", type=int, default=None)
@click.option("--agent", default="yuu", show_default=True)
@click.option("--recurring", is_flag=True, default=False)
@click.pass_context
def schedule_create(ctx: click.Context, cron: str, task: tuple[str, ...], ctx_id: int | None, agent: str, recurring: bool) -> None:
    """Create a scheduled task."""

    async def _run(cfg):
        from yuubot.services.schedule import ScheduleService

        return await ScheduleService(config=cfg).create(
            {
                "bot_kind": "master",
                    "ctx_id": ctx_id,
                "cron": cron,
                "task": " ".join(task),
                "agent": agent,
                "recurring": recurring,
            }
        )

    result = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    click.echo(f"created schedule {result['id']}")


@schedule.command("list")
@click.option("--all", "show_all", is_flag=True, default=False)
@click.pass_context
def schedule_list(ctx: click.Context, show_all: bool) -> None:
    """List scheduled tasks."""

    async def _run(cfg):
        from yuubot.services.schedule import ScheduleService

        return await ScheduleService(config=cfg).list(
            {"bot_kind": "master", "all": show_all}
        )

    rows = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    for row in rows:
        status = "enabled" if row["enabled"] else "disabled"
        once = "once" if row["once"] else "recurring"
        click.echo(f"[{row['id']}] {status}/{once} {row['cron']} agent={row['agent']} ctx={row['ctx_id']} {row['task']}")


@schedule.command("cancel")
@click.argument("schedule_id", type=int)
@click.pass_context
def schedule_cancel(ctx: click.Context, schedule_id: int) -> None:
    """Disable a scheduled task."""

    async def _run(cfg):
        from yuubot.services.schedule import ScheduleService

        return await ScheduleService(config=cfg).cancel(
            {"bot_kind": "master", "schedule_id": schedule_id}
        )

    result = asyncio.run(_with_db(ctx.obj["config_path"], _run))
    click.echo(result["status"])


# ── Docker ───────────────────────────────────────────────────────


@cli.group(cls=BotAwareGroup)
def docker() -> None:
    """Docker container management."""


def _compose_cmd(deploy_dir: Path) -> list[str]:
    return ["docker", "compose", "-f", str(deploy_dir / "compose.yaml")]


def _docker_deploy_dir(cfg, override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    return Path(cfg.docker.deploy_dir).expanduser().resolve()


def _docker_source_root(cfg) -> Path:
    if cfg.docker.source_root:
        return Path(cfg.docker.source_root).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def _docker_update_deployment(
    deploy_dir: Path,
    cfg,
    *,
    health_timeout: int,
    health_check: bool,
) -> None:
    compose = _compose_cmd(deploy_dir)
    subprocess.run([*compose, "build", "yuubot"], cwd=deploy_dir, check=True)
    subprocess.run(
        [*compose, "up", "-d", "--no-deps", "--force-recreate", "yuubot", "traces-ui"],
        cwd=deploy_dir,
        check=True,
    )
    if not health_check:
        return

    api = _daemon_api_url_for_host(cfg)
    if not _wait_daemon_api(api, health_timeout):
        raise click.ClickException(
            f"yuubot 容器已替换，但 daemon 未在 {health_timeout}s 内通过健康检查：{api}/health"
        )


def _docker_compose_logs(deploy_dir: Path, service: str) -> str:
    result = subprocess.run(
        [*_compose_cmd(deploy_dir), "logs", "--no-color", service],
        cwd=deploy_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout or ""


def _extract_docker_napcat_login_text(log_text: str) -> str | None:
    from yuubot.napcat import _extract_qr_block, _strip_ansi

    cleaned = _strip_ansi(log_text)
    qr_block = _extract_qr_block(cleaned).strip()
    if qr_block:
        return qr_block

    matched: list[str] = []
    for line in cleaned.splitlines():
        if "WebUi Token:" in line or "WebUi User Panel Url:" in line or "二维码已更新" in line:
            matched.append(line.strip())
    if matched:
        return "\n".join(matched)
    return None


def _print_docker_napcat_login_output(
    deploy_dir: Path,
    *,
    webui_port: int,
    timeout: int = 30,
) -> None:
    host_webui = f"http://localhost:{webui_port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = _extract_docker_napcat_login_text(_docker_compose_logs(deploy_dir, "napcat"))
        if text:
            rendered = re.sub(r"http://0\.0\.0\.0:\d+", host_webui, text)
            click.echo()
            click.echo(rendered)
            click.echo(f"NapCat WebUI: {host_webui}")
            click.echo(f"查看 NapCat 登录输出: docker compose -f {deploy_dir / 'compose.yaml'} logs -f napcat")
            click.echo()
            return
        time.sleep(1)

    click.echo()
    click.echo(f"NapCat WebUI: {host_webui}")
    click.echo(f"查看 NapCat 登录输出: docker compose -f {deploy_dir / 'compose.yaml'} logs -f napcat")
    click.echo()


@docker.command("init")
@click.option(
    "--deploy-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Docker 部署目录（默认读取 config.yaml 的 docker.deploy_dir）",
)
@click.option(
    "--import-archive",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="复制到部署目录的初始化导入包",
)
@click.pass_context
def docker_init(ctx: click.Context, deploy_dir: Path | None, import_archive: Path | None) -> None:
    """Generate standard Docker deployment files."""
    from yuubot.config import load_config
    from yuubot.docker_deploy import write_deployment_bundle

    cfg = load_config(ctx.obj["config_path"])
    deployment = write_deployment_bundle(
        cfg,
        deploy_dir=_docker_deploy_dir(cfg, deploy_dir),
        repo_root=_docker_source_root(cfg),
        import_archive=import_archive,
    )
    click.echo(f"Docker deployment written: {deployment.deploy_dir}")
    click.echo(f"Compose: {deployment.compose_path}")
    click.echo(f"Config: {deployment.config_path}")
    if deployment.import_path is not None:
        click.echo(f"Import archive: {deployment.import_path}")


@docker.command("install")
@click.option(
    "--deploy-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Docker 部署目录（默认读取 config.yaml 的 docker.deploy_dir）",
)
@click.option(
    "--import-archive",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="启动前导入的标准数据包",
)
@click.option("--build/--no-build", default=True, show_default=True, help="是否构建 yuubot 镜像")
@click.pass_context
def docker_install(
    ctx: click.Context,
    deploy_dir: Path | None,
    import_archive: Path | None,
    build: bool,
) -> None:
    """Generate, optionally import, and start the Docker deployment."""
    from yuubot.config import load_config
    from yuubot.docker_deploy import write_deployment_bundle

    cfg = load_config(ctx.obj["config_path"])
    deployment = write_deployment_bundle(
        cfg,
        deploy_dir=_docker_deploy_dir(cfg, deploy_dir),
        repo_root=_docker_source_root(cfg),
        import_archive=import_archive,
    )
    compose = _compose_cmd(deployment.deploy_dir)
    if build:
        subprocess.run([*compose, "build"], cwd=deployment.deploy_dir, check=True)
    if deployment.import_path is not None:
        subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "ybot",
                "yuubot",
                "-c",
                "/config/config.yaml",
                "import",
                f"/import/{deployment.import_path.name}",
            ],
            cwd=deployment.deploy_dir,
            check=True,
        )
    subprocess.run([*compose, "up", "-d"], cwd=deployment.deploy_dir, check=True)
    click.echo(f"Docker yuubot started from {deployment.deploy_dir}")
    _print_docker_napcat_login_output(
        deployment.deploy_dir,
        webui_port=cfg.recorder.napcat_webui_port,
    )


@docker.command("update")
@click.option("--health-timeout", type=int, default=None, help="等待 daemon 健康检查的秒数（默认读取 docker.health_timeout）")
@click.option("--no-health-check", is_flag=True, default=False, help="替换容器后不等待 daemon /health")
@click.pass_context
def docker_update(
    ctx: click.Context,
    health_timeout: int | None,
    no_health_check: bool,
) -> None:
    """Rebuild from current source and replace only the yuubot container."""
    from yuubot.config import load_config
    from yuubot.docker_deploy import write_deployment_bundle

    cfg = load_config(ctx.obj["config_path"])
    deployment = write_deployment_bundle(
        cfg,
        deploy_dir=_docker_deploy_dir(cfg),
        repo_root=_docker_source_root(cfg),
    )
    click.echo("Rebuilding yuubot image from current source...")
    click.echo(f"Deploy dir: {deployment.deploy_dir}")
    click.echo(f"Source root: {_docker_source_root(cfg)}")
    click.echo("NapCat and persisted data volumes will be kept.")
    _docker_update_deployment(
        deployment.deploy_dir,
        cfg,
        health_timeout=health_timeout if health_timeout is not None else cfg.docker.health_timeout,
        health_check=not no_health_check,
    )
    click.echo(f"Docker yuubot updated from {deployment.deploy_dir}")


@docker.command("up")
@click.option(
    "--deploy-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Docker 部署目录（默认读取 config.yaml 的 docker.deploy_dir）",
)
@click.pass_context
def docker_up(ctx: click.Context, deploy_dir: Path | None) -> None:
    """Start the standard Docker deployment and surface NapCat login output."""
    from yuubot.config import load_config

    cfg = load_config(ctx.obj["config_path"])
    target = _docker_deploy_dir(cfg, deploy_dir)
    subprocess.run([*_compose_cmd(target), "up", "-d"], cwd=target, check=True)
    click.echo(f"Docker yuubot started from {target}")
    _print_docker_napcat_login_output(
        target,
        webui_port=cfg.recorder.napcat_webui_port,
    )


@docker.command("down")
@click.option(
    "--deploy-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Docker 部署目录（默认读取 config.yaml 的 docker.deploy_dir）",
)
@click.pass_context
def docker_down(ctx: click.Context, deploy_dir: Path | None) -> None:
    """Stop the standard Docker deployment."""
    from yuubot.config import load_config

    cfg = load_config(ctx.obj["config_path"])
    target = _docker_deploy_dir(cfg, deploy_dir)
    subprocess.run([*_compose_cmd(target), "down"], cwd=target, check=True)
