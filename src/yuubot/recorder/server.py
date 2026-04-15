"""Recorder main — reverse WS server receiving NapCat events, store + relay."""

import asyncio
import json
import re
from datetime import datetime, timezone

import uvicorn
import websockets
from websockets.asyncio.server import Server, ServerConnection

from loguru import logger

from yuubot.config import load_config
from yuubot.core.context import ContextManager
from yuubot.core.db import init_db, close_db
from yuubot.core.models import GroupSetting, MessageEvent, MessageRecord, segments_to_json, segments_to_plain
from yuubot.core.onebot import parse_event, parse_poke_notice
from yuubot.recorder.api import create_api
from yuubot.recorder.downloader import MediaDownloader
from yuubot.recorder.forward import ForwardResolver, _render_forward_log_lines
from yuubot.recorder.relay import RelayServer
from yuubot.recorder.store import Store
from yuubot.log import setup as setup_logging


def _log_raw_event(raw: dict) -> None:
    """Log the original NapCat payload for replay/debugging."""
    logger.debug("NapCat raw event: {}", json.dumps(raw, ensure_ascii=False, sort_keys=True))


def _inject_local_paths(raw_segments: list[dict], media_paths: list[str]) -> None:
    """Inject downloaded local_path into raw OneBot image segments in-place."""
    idx = 0
    for seg in raw_segments:
        if seg.get("type") == "image" and idx < len(media_paths):
            seg.setdefault("data", {})["local_path"] = media_paths[idx]
            idx += 1


def _build_bot_cmd_re(entries: list[str]) -> re.Pattern:
    """Build regex matching ``/ybot off`` or ``/y bot off`` etc.

    Captures group 1 = ``on`` | ``off``.
    """
    escaped = [re.escape(e) for e in sorted(entries, key=lambda x: -len(x))]
    prefix = "|".join(escaped)
    return re.compile(rf"^(?:{prefix})\s*bot\s+(on|off)\b", re.IGNORECASE)


def _extract_plain_text(raw_message: list[dict]) -> str:
    """Extract plain text from raw OneBot message segments."""
    parts = []
    for seg in raw_message:
        if seg.get("type") == "text":
            parts.append(seg.get("data", {}).get("text", ""))
    return "".join(parts).strip()


class NapCatWSServer:
    """Reverse WS server — NapCat connects here to push events."""

    def __init__(
        self,
        store: Store,
        relay: RelayServer,
        muted_ctxs: set[int],
        entries: list[str],
    ) -> None:
        self.store = store
        self.relay = relay
        self._muted_ctxs = muted_ctxs
        self._bot_cmd_re = _build_bot_cmd_re(entries)
        self._server: Server | None = None

    async def start(self, host: str, port: int) -> None:
        self._server = await websockets.serve(self._handler, host, port)
        logger.info("NapCat WS listening on {}:{}", host, port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws: ServerConnection) -> None:
        logger.info("NapCat connected")
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    await self._on_event(data)
                except Exception:
                    logger.exception("Error processing event")
        finally:
            logger.info("NapCat disconnected")

    async def _on_event(self, raw: dict) -> None:
        _log_raw_event(raw)
        event = parse_event(raw)
        if isinstance(event, MessageEvent):
            ctx_id, media_paths, forward_logs = await self.store.save(event)
            # Enrich raw data with ctx_id and local media paths before relaying
            raw["ctx_id"] = ctx_id

            # Emergency brake: /bot off mutes ctx, /bot on unmutes
            plain = _extract_plain_text(raw.get("message", []))
            m = self._bot_cmd_re.match(plain)
            if m:
                action = m.group(1).lower()
                if action == "off":
                    self._muted_ctxs.add(ctx_id)
                    logger.info("ctx {} muted (bot off)", ctx_id)
                elif action == "on":
                    self._muted_ctxs.discard(ctx_id)
                    logger.info("ctx {} unmuted (bot on)", ctx_id)

            if media_paths:
                _inject_local_paths(raw.get("message", []), media_paths)
            for item in forward_logs:
                for line in _render_forward_log_lines(item["forward_id"], item["nodes"], max_depth=3):
                    logger.info(line)
            await self.relay.broadcast(raw)
        elif event is not None:
            # Store poke notices as synthetic messages for browse history
            if raw.get("notice_type") == "notify" and raw.get("sub_type") == "poke":
                await self._store_poke(raw)
            # Non-message events: relay as-is
            await self.relay.broadcast(raw)


    async def _store_poke(self, raw: dict) -> None:
        """Store a poke notice event as a synthetic MessageRecord."""
        try:
            poke = parse_poke_notice(raw)
            if poke is None:
                return
            group_id = raw.get("group_id", 0)
            if not group_id:
                return
            ctx_id = await self.store.ctx_mgr.get_or_create("group", group_id)
            segments: list = [poke]
            ts = datetime.fromtimestamp(raw.get("time", 0), tz=timezone.utc)
            await MessageRecord.create(
                message_id=None,
                ctx_id=ctx_id,
                user_id=int(poke.sender_qq),
                nickname="",
                display_name="",
                content=segments_to_plain(segments),
                raw_message=segments_to_json(segments),
                timestamp=ts,
                media_files=[],
            )
            logger.debug("Stored poke: {} → {} in ctx={}", poke.sender_qq, poke.target_qq, ctx_id)
        except Exception:
            logger.exception("Failed to store poke event")


async def load_muted_ctxs(ctx_mgr: ContextManager) -> set[int]:
    """Load disabled group contexts from DB for recorder-side mute enforcement."""
    muted_ctxs: set[int] = set()
    disabled_groups = await GroupSetting.filter(bot_enabled=False).values_list("group_id", flat=True)
    for group_id in disabled_groups:
        raw_group_id = group_id[0] if isinstance(group_id, tuple) else group_id
        ctx_id = await ctx_mgr.get_or_create("group", int(raw_group_id))
        muted_ctxs.add(ctx_id)
    logger.info("Loaded {} muted contexts from DB", len(muted_ctxs))
    return muted_ctxs


async def run_recorder(config_path: str | None = None) -> None:
    """Entry point: start recorder (WS server + relay + HTTP API)."""
    cfg = load_config(config_path)
    setup_logging(cfg.log_dir, name="recorder")
    await init_db(cfg.database.path)
    ctx_mgr = ContextManager()
    await ctx_mgr.load()

    muted_ctxs = await load_muted_ctxs(ctx_mgr)

    downloader = MediaDownloader(
        cfg.recorder.media_dir,
        qq_direct=cfg.network.qq_direct,
    )
    forward_resolver = ForwardResolver(cfg.recorder.napcat_http)
    store = Store(ctx_mgr=ctx_mgr, downloader=downloader, forward_resolver=forward_resolver)
    relay = RelayServer()
    napcat_ws = NapCatWSServer(
        store=store, relay=relay, muted_ctxs=muted_ctxs, entries=cfg.bot.entries,
    )

    shutdown_event = asyncio.Event()
    api_app = create_api(
        cfg.recorder.napcat_http, ctx_mgr, shutdown_event,
        bot_qq=cfg.bot.qq, master_qq=cfg.bot.master, muted_ctxs=muted_ctxs,
    )

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

    logger.info("Recorder running. Press Ctrl+C or POST /shutdown to stop.")
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
        await forward_resolver.close()
        await close_db()
        logger.info("Recorder stopped.")
