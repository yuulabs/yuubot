"""HTTP API proxy — sits between skills/daemon and NapCat HTTP API."""

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from yuubot.core.audit import audit_message, soft_audit_message
from yuubot.core.context import ContextManager
from yuubot.core.models import MessageRecord, PokeSegment, ReactSegment, segments_to_plain, segments_to_json
from yuubot.core.onebot import parse_segments

from loguru import logger


async def _log_bot_msg(body: dict, resp: dict, ctx_mgr: ContextManager, bot_qq: int) -> None:
    """Log the bot's own sent message into the message store."""
    try:
        msg_type = body.get("message_type", "private")
        target_id = body.get("group_id") or body.get("user_id") or 0
        ctx_id = await ctx_mgr.get_or_create(msg_type, int(target_id))

        segments = parse_segments(body.get("message", []))
        plain = segments_to_plain(segments)
        raw_json = segments_to_json(segments)
        message_id = resp.get("data", {}).get("message_id")

        await MessageRecord.create(
            message_id=message_id,
            ctx_id=ctx_id,
            user_id=bot_qq,
            nickname="bot",
            display_name="bot",
            content=plain,
            raw_message=raw_json,
            timestamp=datetime.now(tz=timezone.utc),
            media_files=[],
        )
        logger.info("Bot msg logged: ctx={} content={}", ctx_id, plain[:80])
    except Exception:
        logger.exception("Failed to log bot message")


async def _log_bot_action(
    *,
    ctx_id: int,
    bot_qq: int,
    segments: list,
    message_id: int | None = None,
) -> None:
    """Log a synthetic bot-side action into the message store."""
    try:
        await MessageRecord.create(
            message_id=message_id,
            ctx_id=ctx_id,
            user_id=bot_qq,
            nickname="bot",
            display_name="bot",
            content=segments_to_plain(segments),
            raw_message=segments_to_json(segments),
            timestamp=datetime.now(tz=timezone.utc),
            media_files=[],
        )
    except Exception:
        logger.exception("Failed to log bot action")


async def _send_via_pipeline(
    *,
    client: httpx.AsyncClient,
    body: dict,
    ctx_mgr: ContextManager,
    bot_qq: int,
) -> tuple[dict, int]:
    """Send outbound actions, extracting synthetic segments handled by recorder."""
    raw_segments = body.get("message", [])
    react_segs = [seg for seg in raw_segments if seg.get("type") == "react"]
    normal_body = {
        **body,
        "message": [seg for seg in raw_segments if seg.get("type") != "react"],
    }

    last_status = 200
    last_data: dict = {"status": "ok", "retcode": 0}

    if normal_body["message"]:
        r = await client.post("/send_msg", json=normal_body)
        last_status = r.status_code
        last_data = r.json()
        if r.status_code == 200:
            await _log_bot_msg(normal_body, last_data, ctx_mgr, bot_qq)
        else:
            return last_data, last_status

    for react in react_segs:
        react_data = react.get("data", {})
        react_body = {
            "message_id": react_data.get("message_id"),
            "emoji_id": react_data.get("emoji_id"),
        }
        r = await client.post("/set_msg_emoji_like", json=react_body)
        last_status = r.status_code
        last_data = r.json()
        if r.status_code != 200:
            return last_data, last_status

        if bot_qq:
            record = await MessageRecord.filter(message_id=react_body["message_id"]).order_by("-id").first()
            if record is not None:
                await _log_bot_action(
                    ctx_id=record.ctx_id,
                    bot_qq=bot_qq,
                    segments=[ReactSegment(
                        message_id=str(react_body["message_id"]),
                        emoji_id=str(react_body["emoji_id"]),
                    )],
                )

    return last_data, last_status


def create_api(
    napcat_http: str,
    ctx_mgr: ContextManager,
    shutdown_event,
    bot_qq: int = 0,
    master_qq: int = 0,
    muted_ctxs: set[int] | None = None,
) -> FastAPI:
    client = httpx.AsyncClient(base_url=napcat_http, timeout=30)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="yuubot-recorder-api", lifespan=lifespan)

    if muted_ctxs is None:
        muted_ctxs = set()

    GROUP_RATE_WINDOW = 60  # seconds
    GROUP_RATE_LIMIT = 5
    group_send_ts: dict[int, deque[float]] = defaultdict(deque)

    def _check_rate_limit(gid: int) -> tuple[bool, int]:
        """Check and update rate limit for group gid. Returns (allowed, remaining)."""
        now = time.monotonic()
        window = group_send_ts[gid]
        while window and now - window[0] > GROUP_RATE_WINDOW:
            window.popleft()
        if len(window) >= GROUP_RATE_LIMIT:
            return False, 0
        window.append(now)
        return True, GROUP_RATE_LIMIT - len(window)

    @app.post("/send_msg")
    async def send_msg(request: Request) -> JSONResponse:
        body = await request.json()

        # Mute check — emergency brake triggered by /bot off
        msg_type = body.get("message_type", "private")
        target_id = body.get("group_id") or body.get("user_id") or 0
        mute_ctx = ctx_mgr.lookup(msg_type, int(target_id))
        if mute_ctx is not None and mute_ctx in muted_ctxs:
            logger.warning("send_msg blocked: ctx {} is muted", mute_ctx)
            return JSONResponse({"error": f"ctx {mute_ctx} is muted"}, status_code=403)

        # Content audit — block sensitive info leaks
        segments = body.get("message", [])
        result = audit_message(segments)
        is_master_private = (
            master_qq != 0
            and body.get("message_type") == "private"
            and body.get("user_id") == master_qq
        )
        if not result.passed and not is_master_private:
            logger.warning("安全审查拦截: {} | match={} | body={}", result.category, result.match, body)
            error_msg = f"安全审查拦截: 消息包含{result.category}，请勿泄露敏感信息"
            return JSONResponse({"error": error_msg}, status_code=403)

        # Soft audit — structured privacy data (bot mode only, skip master private)
        if request.headers.get("X-Bot-Mode") == "1" and not is_master_private:
            soft_result = soft_audit_message(segments)
            if not soft_result.passed:
                logger.warning("软审查拦截: {} | match={} | body={}", soft_result.category, soft_result.match, body)
                error_msg = f"安全审查拦截: {soft_result.category}"
                return JSONResponse({"error": error_msg}, status_code=403)

        # Rate limit group sends/actions
        if body.get("message_type") == "group":
            gid = body.get("group_id", 0)
            allowed, remaining = _check_rate_limit(gid)
            if not allowed:
                return JSONResponse(
                    {"error": "群聊限流: 每分钟最多5条", "remaining": 0},
                    status_code=429,
                )

            data, status_code = await _send_via_pipeline(
                client=client, body=body, ctx_mgr=ctx_mgr, bot_qq=bot_qq,
            )
            data["remaining"] = remaining
            return JSONResponse(data, status_code=status_code)

        data, status_code = await _send_via_pipeline(
            client=client, body=body, ctx_mgr=ctx_mgr, bot_qq=bot_qq,
        )
        return JSONResponse(data, status_code=status_code)

    @app.get("/get_group_list")
    async def get_group_list() -> JSONResponse:
        r = await client.get("/get_group_list")
        return JSONResponse(r.json())

    @app.get("/get_friend_list")
    async def get_friend_list() -> JSONResponse:
        r = await client.get("/get_friend_list")
        return JSONResponse(r.json())

    @app.get("/get_login_info")
    async def get_login_info() -> JSONResponse:
        r = await client.get("/get_login_info")
        return JSONResponse(r.json())

    @app.get("/get_group_member_list")
    async def get_group_member_list(group_id: int) -> JSONResponse:
        r = await client.get("/get_group_member_list", params={"group_id": group_id})
        return JSONResponse(r.json())

    @app.post("/group_poke")
    async def group_poke(request: Request) -> JSONResponse:
        body = await request.json()
        # Rate limit group poke actions
        gid = body.get("group_id", 0)
        if gid:
            allowed, remaining = _check_rate_limit(gid)
            if not allowed:
                return JSONResponse(
                    {"error": "群聊限流: 每分钟最多5条", "remaining": 0},
                    status_code=429,
                )
        r = await client.post("/group_poke", json=body)
        data = r.json()
        data["remaining"] = remaining if gid else 0
        if r.status_code == 200 and gid and bot_qq:
            await _log_bot_action(
                ctx_id=await ctx_mgr.get_or_create("group", int(gid)),
                bot_qq=bot_qq,
                segments=[PokeSegment(sender_qq=str(bot_qq), target_qq=str(body.get("user_id", 0)))],
            )
        return JSONResponse(data, status_code=r.status_code)

    @app.get("/ctx/{ctx_id}")
    async def get_ctx(ctx_id: int) -> JSONResponse:
        info = ctx_mgr.resolve(ctx_id)
        if info is None:
            return JSONResponse({"error": "ctx not found"}, status_code=404)
        return JSONResponse({"ctx_id": info.ctx_id, "type": info.type, "target_id": info.target_id})

    @app.get("/ctx")
    async def list_ctx() -> JSONResponse:
        items = [{"ctx_id": c.ctx_id, "type": c.type, "target_id": c.target_id} for c in ctx_mgr.all()]
        return JSONResponse(items)

    # ── Guaranteed delivery queue ────────────────────────────────────

    guaranteed_queues: dict[int, asyncio.Queue] = {}
    guaranteed_drain_tasks: dict[int, asyncio.Task] = {}

    async def _drain_guaranteed_queue(gid: int) -> None:
        """Background task that drains queue when rate limit allows."""
        queue = guaranteed_queues.get(gid)
        if queue is None:
            return
        try:
            while True:
                item = await queue.get()
                try:
                    # Wait for rate limit slot (polling approach)
                    while True:
                        allowed, remaining = _check_rate_limit(gid)
                        if allowed:
                            break
                        await asyncio.sleep(1)
                    # Send the message via internal send_msg logic (skip rate limit)
                    body = item["body"]
                    data, status_code = await _send_via_pipeline(
                        client=client, body=body, ctx_mgr=ctx_mgr, bot_qq=bot_qq,
                    )
                    data["remaining"] = remaining
                    logger.debug("Guaranteed msg sent: gid={} remaining={}", gid, remaining)
                    if status_code != 200:
                        logger.warning("Guaranteed msg send failed: gid={} status={}", gid, status_code)
                except Exception:
                    logger.exception("Failed to send guaranteed msg for gid={}", gid)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            pass

    def _ensure_drain_task(gid: int) -> None:
        """Lazily start drain task for a group queue."""
        if gid not in guaranteed_queues:
            guaranteed_queues[gid] = asyncio.Queue()
        if gid not in guaranteed_drain_tasks or guaranteed_drain_tasks[gid].done():
            guaranteed_drain_tasks[gid] = asyncio.create_task(
                _drain_guaranteed_queue(gid),
                name=f"guaranteed-drain-{gid}",
            )

    @app.post("/send_msg_guaranteed")
    async def send_msg_guaranteed(request: Request) -> JSONResponse:
        """Queue message for guaranteed delivery. Returns immediately.

        Group messages are queued and sent FIFO when rate limit allows.
        Private messages are sent immediately (no rate limit).
        No 429 responses — guaranteed eventual delivery.
        """
        body = await request.json()

        # Mute check — still applies to guaranteed messages
        msg_type = body.get("message_type", "private")
        target_id = body.get("group_id") or body.get("user_id") or 0
        mute_ctx = ctx_mgr.lookup(msg_type, int(target_id))
        if mute_ctx is not None and mute_ctx in muted_ctxs:
            logger.warning("send_msg_guaranteed blocked: ctx {} is muted", mute_ctx)
            return JSONResponse({"error": f"ctx {mute_ctx} is muted"}, status_code=403)

        # Content audit — still applies
        segments = body.get("message", [])
        result = audit_message(segments)
        is_master_private = (
            master_qq != 0
            and body.get("message_type") == "private"
            and body.get("user_id") == master_qq
        )
        if not result.passed and not is_master_private:
            logger.warning("安全审查拦截: {} | match={} | body={}", result.category, result.match, body)
            error_msg = f"安全审查拦截: 消息包含{result.category}，请勿泄露敏感信息"
            return JSONResponse({"error": error_msg}, status_code=403)

        # Soft audit
        if request.headers.get("X-Bot-Mode") == "1" and not is_master_private:
            soft_result = soft_audit_message(segments)
            if not soft_result.passed:
                logger.warning("软审查拦截: {} | match={} | body={}", soft_result.category, soft_result.match, body)
                error_msg = f"安全审查拦截: {soft_result.category}"
                return JSONResponse({"error": error_msg}, status_code=403)

        # Private messages — send immediately, no queue
        if body.get("message_type") != "group":
            data, status_code = await _send_via_pipeline(
                client=client, body=body, ctx_mgr=ctx_mgr, bot_qq=bot_qq,
            )
            return JSONResponse(data, status_code=status_code)

        # Group messages — queue for guaranteed delivery
        gid = body.get("group_id", 0)
        if gid == 0:
            return JSONResponse({"error": "group_id required for group messages"}, status_code=400)

        _ensure_drain_task(gid)
        queue_size = guaranteed_queues[gid].qsize()
        guaranteed_queues[gid].put_nowait({"body": body})
        logger.info("Guaranteed msg queued: gid={} queue_size={}", gid, queue_size + 1)
        return JSONResponse({"queued": True, "group_id": gid, "queue_size": queue_size + 1})

    @app.post("/shutdown")
    async def do_shutdown() -> JSONResponse:
        shutdown_event.set()
        return JSONResponse({"status": "shutting down"})

    return app
