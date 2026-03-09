"""Recorder main — reverse WS server receiving NapCat events, store + relay."""

import asyncio
import json
import logging

import uvicorn
import websockets
from websockets.asyncio.server import Server, ServerConnection

from yuubot.config import load_config
from yuubot.core.context import ContextManager
from yuubot.core.db import init_db, close_db
from yuubot.core.models import MessageEvent
from yuubot.core.onebot import parse_event
from yuubot.recorder.api import create_api
from yuubot.recorder.downloader import MediaDownloader
from yuubot.recorder.relay import RelayServer
from yuubot.recorder.store import Store

log = logging.getLogger(__name__)


def _inject_local_paths(raw_segments: list[dict], media_paths: list[str]) -> None:
    """Inject downloaded local_path into raw OneBot image segments in-place."""
    idx = 0
    for seg in raw_segments:
        if seg.get("type") == "image" and idx < len(media_paths):
            seg.setdefault("data", {})["local_path"] = media_paths[idx]
            idx += 1


class NapCatWSServer:
    """Reverse WS server — NapCat connects here to push events."""

    def __init__(self, store: Store, relay: RelayServer) -> None:
        self.store = store
        self.relay = relay
        self._server: Server | None = None

    async def start(self, host: str, port: int) -> None:
        self._server = await websockets.serve(self._handler, host, port)
        log.info("NapCat WS listening on %s:%d", host, port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws: ServerConnection) -> None:
        log.info("NapCat connected")
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    await self._on_event(data)
                except Exception:
                    log.exception("Error processing event")
        finally:
            log.info("NapCat disconnected")

    async def _on_event(self, raw: dict) -> None:
        event = parse_event(raw)
        if isinstance(event, MessageEvent):
            ctx_id, media_paths = await self.store.save(event)
            # Enrich raw data with ctx_id and local media paths before relaying
            raw["ctx_id"] = ctx_id
            if media_paths:
                _inject_local_paths(raw.get("message", []), media_paths)
            await self.relay.broadcast(raw)
        elif event is not None:
            # Non-message events: relay as-is
            await self.relay.broadcast(raw)


async def run_recorder(config_path: str | None = None) -> None:
    """Entry point: start recorder (WS server + relay + HTTP API)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    cfg = load_config(config_path)
    await init_db(cfg.database.path)
    ctx_mgr = ContextManager()
    await ctx_mgr.load()

    downloader = MediaDownloader(cfg.recorder.media_dir)
    store = Store(ctx_mgr=ctx_mgr, downloader=downloader)
    relay = RelayServer()
    napcat_ws = NapCatWSServer(store=store, relay=relay)

    shutdown_event = asyncio.Event()
    api_app = create_api(cfg.recorder.napcat_http, ctx_mgr, shutdown_event, bot_qq=cfg.bot.qq, master_qq=cfg.bot.master)

    # Start all services
    await napcat_ws.start(cfg.recorder.napcat_ws.host, cfg.recorder.napcat_ws.port)
    await relay.start(cfg.recorder.relay_ws.host, cfg.recorder.relay_ws.port)

    api_config = uvicorn.Config(
        api_app,
        host=cfg.recorder.api.host,
        port=cfg.recorder.api.port,
        log_level="info",
    )
    api_server = uvicorn.Server(api_config)

    api_task = asyncio.create_task(api_server.serve())

    log.info("Recorder running. Press Ctrl+C or POST /shutdown to stop.")
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        api_server.should_exit = True
        await api_task
        await napcat_ws.stop()
        await relay.stop()
        await downloader.close()
        await close_db()
        log.info("Recorder stopped.")
