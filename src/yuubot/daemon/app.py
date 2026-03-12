"""Daemon FastAPI app — main bot process."""

import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from loguru import logger

from yuubot.commands.builtin import build_command_tree
from yuubot.daemon.llm import LLMExecutor
from yuubot.commands.entry import EntryManager
from yuubot.commands.roles import RoleManager
from yuubot.config import load_config
from yuubot.core.db import init_db, close_db
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.dispatcher import Dispatcher
from yuubot.daemon.scheduler import Scheduler
from yuubot.daemon.conversation import ConversationManager
from yuubot.daemon.ws_client import WSClient
from yuubot.log import setup as setup_logging


def _init_tracing() -> None:
    try:
        import yuutrace
    except ImportError:
        return
    yuutrace.init(service_name="yuubot-daemon")


async def run_daemon(config_path: str | None = None) -> None:
    _init_tracing()

    cfg = load_config(config_path)
    setup_logging(cfg.log_dir)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)

    role_mgr = RoleManager(master_qq=cfg.bot.master)
    entry_mgr = EntryManager()
    agent_runner = AgentRunner(config=cfg)
    conv_mgr = ConversationManager(
        ttl=float(cfg.session.ttl),
        max_tokens=cfg.session.max_tokens,
    )
    await conv_mgr.load_auto()
    conv_mgr._is_ctx_active = lambda ctx_id: agent_runner.get_active_flow(ctx_id) is not None

    llm_exec = LLMExecutor(
        conv_mgr=conv_mgr,
        agent_runner=agent_runner,
        config=cfg,
        role_mgr=role_mgr,
    )
    root = build_command_tree(cfg.bot.entries, llm_executor=llm_exec)

    deps = {
        "role_mgr": role_mgr,
        "entry_mgr": entry_mgr,
        "root": root,
        "dm_whitelist": cfg.response.dm_whitelist,
        "session_mgr": conv_mgr,
        "config": cfg,
        "agent_runner": agent_runner,
    }

    dispatcher = Dispatcher(
        config=cfg,
        root=root,
        role_mgr=role_mgr,
        deps=deps,
        agent_runner=agent_runner,
        conv_mgr=conv_mgr,
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

    logger.info("Daemon running. POST /shutdown to stop.")
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
        logger.info("Daemon stopped.")
