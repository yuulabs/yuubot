"""Daemon FastAPI app — RFC2 yuuagents skeleton process."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
import uvicorn
import yuutrace
from yuutrace.cli.server import _build_app

from yuubot.admin.app import create_admin_app
from yuubot.channels.qq import QQRecorderAdapter
from yuubot.channels.web import WebChatAdapter
from yuubot.admin.persist import setup_persistent_paths
from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager
from yuubot.config import load_config
from yuubot.core.db import close_db, init_db
from yuubot.daemon.dispatcher import Dispatcher
from yuubot.daemon.gateway import Gateway, OutboundMessage, build_routing_engine
from yuubot.daemon.llm import LLMExecutor
from yuubot.daemon.local_api import create_agent_fn_router
from yuubot.daemon.ws_client import WSClient
from yuubot.log import setup as setup_logging

_YTRACE_HOST = "127.0.0.1"
_YTRACE_PORT = 4318
_YTRACE_DB_PATH = "~/.yagents/traces.db"


async def _upgrade_genai_prices() -> None:
    import importlib
    import sys

    logger.info("Upgrading genai-prices...")
    for cmd in (
        ["uv", "pip", "install", "--upgrade", "genai-prices"],
        [sys.executable, "-m", "pip", "install", "--upgrade", "genai-prices"],
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                break
            logger.debug("genai-prices upgrade via {} failed: {}", cmd[0], stderr.decode()[:200])
        except FileNotFoundError:
            continue
    else:
        logger.warning("genai-prices upgrade failed with all methods")
        return

    try:
        import genai_prices.data_snapshot as _ds
        importlib.reload(_ds)
        logger.info("genai-prices upgraded and snapshot reloaded")
    except Exception:
        logger.warning("genai-prices upgraded but module reload failed; takes effect on restart")


async def _daily_genai_prices_updater() -> None:
    await asyncio.sleep(24 * 3600)
    while True:
        await _upgrade_genai_prices()
        await asyncio.sleep(24 * 3600)


def _trace_db_path(cfg) -> str:
    trace_cfg = cfg.yuuagents.get("yuutrace")
    db_path = trace_cfg.get("db_path") if isinstance(trace_cfg, dict) else None
    return str(Path(db_path or _YTRACE_DB_PATH).expanduser())


async def _start_tracing(cfg) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    db_path = _trace_db_path(cfg)
    trace_config = uvicorn.Config(
        _build_app(db_path),
        host=_YTRACE_HOST,
        port=_YTRACE_PORT,
        log_level="info",
    )
    trace_server = uvicorn.Server(trace_config)
    trace_task = asyncio.create_task(trace_server.serve(), name="ytrace-server")
    yuutrace.init(service_name="yuubot-daemon")
    logger.info(
        "YuuTrace server starting on {}:{} (db: {})",
        _YTRACE_HOST,
        _YTRACE_PORT,
        db_path,
    )
    return trace_server, trace_task


def _build_routing(cfg):
    from yuubot.daemon.gateway import RoutingRule
    routing = cfg.routing
    rules = [
        RoutingRule(match=r.get("match", {}), actor=r.get("actor", "yuu"))
        for r in routing.rules
        if isinstance(r, dict)
    ]
    return build_routing_engine({
        "defaults": {
            "group": routing.defaults.group,
            "private": routing.defaults.private,
            "thread": routing.defaults.thread,
            "session": routing.defaults.session,
            "other": routing.defaults.other,
        },
        "rules": [{"match": r.match, "actor": r.actor} for r in rules],
    })


async def run_daemon(config_path: str | None = None) -> None:
    cfg = load_config(config_path)
    setup_logging(cfg.log_dir, name="daemon")
    trace_server, trace_task = await _start_tracing(cfg)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    await setup_persistent_paths(cfg.admin.persistent_paths, cfg.admin.persist_base)

    from yuubot.admin.config_api import load_config_overrides
    from yuubot.core.models import Context
    await load_config_overrides(cfg)
    await Context.get_or_create(
        channel="web",
        key="session:admin",
        defaults={
            "kind": "session",
            "label": "Admin session",
            "type": "web",
            "target_id": 0,
        },
    )

    from yuubot.model_resolution import load_model_pricing
    await load_model_pricing()
    asyncio.create_task(_daily_genai_prices_updater(), name="genai-prices-updater")

    entry_mgr = EntryManager()

    # ── Stage + Actor architecture (yuuagents v0.2.0) ──
    from yuubot.daemon.actor import build_master_stage, build_group_stage, YuubotActor

    master_stage = build_master_stage(cfg)
    group_stage = build_group_stage(cfg)

    send_gateway = Gateway()
    web_adapter = WebChatAdapter()
    send_gateway.register(QQRecorderAdapter(cfg.daemon.recorder_api))
    send_gateway.register(web_adapter)

    async def _send_actor_reply(ctx_id: int, message: OutboundMessage) -> None:
        await send_gateway.send(ctx_id, message)

    master_actor = YuubotActor(master_stage, bot_kind="master", config=cfg, im_sender=_send_actor_reply)
    group_actor = YuubotActor(group_stage, bot_kind="group", config=cfg, im_sender=_send_actor_reply)
    # ─────────────────────────────────────────────────────────────

    llm_exec = LLMExecutor(
        config=cfg,
        master_actor=master_actor,
        group_actor=group_actor,
        routing_engine=_build_routing(cfg),
    )
    root = build_command_tree(cfg.bot.entries, llm_executor=llm_exec)
    deps: dict[str, object] = {
        "entry_mgr": entry_mgr,
        "root": root,
        "dm_whitelist": cfg.response.dm_whitelist,
        "config": cfg,
        "master_actor": master_actor,
        "group_actor": group_actor,
    }
    dispatcher = Dispatcher(
        config=cfg,
        root=root,
        deps=deps,
        master_actor=master_actor,
        group_actor=group_actor,
    )
    deps["dispatcher"] = dispatcher

    ws_client = WSClient(url=cfg.daemon.recorder_ws, on_event=dispatcher.dispatch)
    shutdown_event = asyncio.Event()

    app = FastAPI(title="yuubot-daemon")
    app.include_router(create_agent_fn_router(config=cfg))

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "workers": len(dispatcher._workers),
                "live_agents": len(master_actor.agents) + len(group_actor.agents),
                "runtime": "rfc2",
            }
        )

    @app.post("/shutdown")
    async def do_shutdown() -> JSONResponse:
        shutdown_event.set()
        return JSONResponse({"status": "shutting down"})

    _allowed_roots = [
        p for raw in [
            cfg.recorder.media_dir,
            str(cfg.yuuagents.get("workspace_root", "")),
            cfg.web.download_dir,
        ] if raw
        for p in [Path(raw).expanduser().resolve()]
    ]

    @app.get("/internal/serve")
    async def serve_file(path: str = Query(...)) -> FileResponse:
        target = Path(path).resolve()
        if not any(target == r or r in target.parents for r in _allowed_roots):
            raise HTTPException(status_code=403, detail="path not in allowed directories")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target)

    await ws_client.connect()
    dispatcher.start()
    asyncio.create_task(master_actor.run(), name="master-actor")
    asyncio.create_task(group_actor.run(), name="group-actor")

    api_config = uvicorn.Config(
        app,
        host=cfg.daemon.api.host,
        port=cfg.daemon.api.port,
        log_level="info",
    )
    api_server = uvicorn.Server(api_config)
    api_task = asyncio.create_task(api_server.serve())

    admin_server: uvicorn.Server | None = None
    admin_task: asyncio.Task | None = None
    if cfg.admin.enabled:
        admin_cfg = uvicorn.Config(
            create_admin_app(cfg, master_actor, web_adapter),
            host=cfg.admin.host,
            port=cfg.admin.port,
            log_level="info",
        )
        admin_server = uvicorn.Server(admin_cfg)
        admin_task = asyncio.create_task(admin_server.serve(), name="admin-server")
        logger.info("Admin panel on {}:{}", cfg.admin.host, cfg.admin.port)

    logger.info("Daemon running with RFC2 yuuagents skeleton.")
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await dispatcher.stop()
        await master_actor.close()
        await group_actor.close()
        await ws_client.close()
        api_server.should_exit = True
        await api_task
        if admin_server is not None and admin_task is not None:
            admin_server.should_exit = True
            await admin_task
        trace_server.should_exit = True
        await trace_task
        await close_db()
        logger.info("Daemon stopped.")
