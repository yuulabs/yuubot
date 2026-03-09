"""HTTP API proxy — sits between skills/daemon and NapCat HTTP API."""

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from yuubot.core.audit import audit_message, soft_audit_message
from yuubot.core.context import ContextManager
from yuubot.core.models import MessageRecord, segments_to_plain, segments_to_json
from yuubot.core.onebot import parse_segments

log = logging.getLogger(__name__)


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
        log.info("Bot msg logged: ctx=%d content=%s", ctx_id, plain[:80])
    except Exception:
        log.exception("Failed to log bot message")


def create_api(napcat_http: str, ctx_mgr: ContextManager, shutdown_event, bot_qq: int = 0, master_qq: int = 0) -> FastAPI:
    app = FastAPI(title="yuubot-recorder-api")
    client = httpx.AsyncClient(base_url=napcat_http, timeout=30)

    GROUP_RATE_WINDOW = 60  # seconds
    GROUP_RATE_LIMIT = 5
    group_send_ts: dict[int, deque[float]] = defaultdict(deque)

    @app.post("/send_msg")
    async def send_msg(request: Request) -> JSONResponse:
        body = await request.json()

        # Content audit — block sensitive info leaks
        segments = body.get("message", [])
        result = audit_message(segments)
        is_master_private = (
            master_qq != 0
            and body.get("message_type") == "private"
            and body.get("user_id") == master_qq
        )
        if not result.passed:
            log.warning("安全审查拦截: %s | match=%r | body=%s", result.category, result.match, body)
            if is_master_private:
                error_msg = (
                    f"安全审查拦截: 消息包含{result.category}，"
                    f"触发片段: {result.match!r}，请修改后重试"
                )
            else:
                error_msg = f"安全审查拦截: 消息包含{result.category}，请勿泄露敏感信息"
            return JSONResponse({"error": error_msg}, status_code=403)

        # Soft audit — structured privacy data (bot mode only)
        if request.headers.get("X-Bot-Mode") == "1":
            soft_result = soft_audit_message(segments)
            if not soft_result.passed:
                log.warning("软审查拦截: %s | match=%r | body=%s", soft_result.category, soft_result.match, body)
                if is_master_private:
                    error_msg = (
                        f"安全审查拦截: {soft_result.category}，"
                        f"触发字段: {soft_result.match!r}，请修改后重试"
                    )
                else:
                    error_msg = f"安全审查拦截: {soft_result.category}"
                return JSONResponse({"error": error_msg}, status_code=403)

        # Rate limit group messages
        if body.get("message_type") == "group":
            gid = body.get("group_id", 0)
            now = time.monotonic()
            window = group_send_ts[gid]
            # Evict expired timestamps
            while window and now - window[0] > GROUP_RATE_WINDOW:
                window.popleft()
            if len(window) >= GROUP_RATE_LIMIT:
                return JSONResponse(
                    {"error": "群聊限流: 每分钟最多5条", "remaining": 0},
                    status_code=429,
                )
            window.append(now)
            remaining = GROUP_RATE_LIMIT - len(window)

            r = await client.post("/send_msg", json=body)
            data = r.json()
            data["remaining"] = remaining
            if r.status_code == 200:
                await _log_bot_msg(body, data, ctx_mgr, bot_qq)
            return JSONResponse(data, status_code=r.status_code)

        r = await client.post("/send_msg", json=body)
        data = r.json()
        if r.status_code == 200:
            await _log_bot_msg(body, data, ctx_mgr, bot_qq)
        return JSONResponse(data, status_code=r.status_code)

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

    @app.post("/shutdown")
    async def do_shutdown() -> JSONResponse:
        shutdown_event.set()
        return JSONResponse({"status": "shutting down"})

    @app.on_event("shutdown")
    async def _close_client() -> None:
        await client.aclose()

    return app
