"""Daemon FastAPI app — main bot process."""

import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager
from yuubot.commands.roles import RoleManager
from yuubot.config import load_config
from yuubot.core.db import init_db, close_db
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.dispatcher import Dispatcher
from yuubot.daemon.scheduler import Scheduler
from yuubot.daemon.session import SessionManager
from yuubot.daemon.ws_client import WSClient

log = logging.getLogger(__name__)


def _init_tracing() -> None:
    try:
        import yuutrace
    except ImportError:
        return
    yuutrace.init(service_name="yuubot-daemon")


async def run_daemon(config_path: str | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    _init_tracing()

    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)

    role_mgr = RoleManager(master_qq=cfg.bot.master)
    entry_mgr = EntryManager()
    agent_runner = AgentRunner(config=cfg)
    session_mgr = SessionManager(
        ttl=float(cfg.session.ttl),
        max_tokens=cfg.session.max_tokens,
    )
    await session_mgr.load_auto()
    session_mgr._is_ctx_active = lambda ctx_id: agent_runner.get_active_flow(ctx_id) is not None
    root = build_command_tree(cfg.bot.entries)

    deps = {
        "role_mgr": role_mgr,
        "entry_mgr": entry_mgr,
        "root": root,
        "dm_whitelist": cfg.response.dm_whitelist,
        "session_mgr": session_mgr,
        "config": cfg,
    }

    dispatcher = Dispatcher(
        config=cfg,
        root=root,
        role_mgr=role_mgr,
        deps=deps,
        agent_runner=agent_runner,
        session_mgr=session_mgr,
    )

    ws_client = WSClient(url=cfg.daemon.recorder_ws, on_event=dispatcher.dispatch)
    scheduler = Scheduler(config=cfg, agent_runner=agent_runner)

    shutdown_event = asyncio.Event()

    app = FastAPI(title="yuubot-daemon")

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "workers": len(dispatcher._workers)})

    @app.post("/shutdown")
    async def do_shutdown() -> JSONResponse:
        shutdown_event.set()
        return JSONResponse({"status": "shutting down"})

    @app.post("/schedule/reload")
    async def schedule_reload() -> JSONResponse:
        await scheduler.reload()
        return JSONResponse({"status": "reloaded"})

    # Start everything
    await ws_client.connect()
    dispatcher.start()
    await scheduler.start()

    api_config = uvicorn.Config(
        app,
        host=cfg.daemon.api.host,
        port=cfg.daemon.api.port,
        log_level="info",
    )
    api_server = uvicorn.Server(api_config)
    api_task = asyncio.create_task(api_server.serve())

    log.info("Daemon running. POST /shutdown to stop.")
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await scheduler.stop()
        await dispatcher.stop()
        await agent_runner.stop()
        await ws_client.close()
        api_server.should_exit = True
        await api_task
        await close_db()
        log.info("Daemon stopped.")
