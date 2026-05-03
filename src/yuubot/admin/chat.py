"""Web chat backend — a WebSocket channel adapter for Admin sessions."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from yuubot.channels.web import WebChatAdapter
from yuubot.daemon.actor import HumanMessage, YuubotActor
from yuubot.daemon.gateway import ContextRef, get_or_create_context


WEB_CTX_TYPE = "web"
WEB_CTX_TARGET = 0  # admin panel is target_id=0, type="web"
WEB_CTX_KEY = "session:admin"
WEB_CHAR = "shiori"


async def _get_or_create_web_ctx() -> int:
    ctx = await get_or_create_context(ContextRef(
        channel="web",
        key=WEB_CTX_KEY,
        kind="session",
        label="Admin session",
        metadata={"type": WEB_CTX_TYPE, "target_id": WEB_CTX_TARGET},
    ))
    return int(ctx.id)


def create_chat_router(
    master_actor: YuubotActor,
    web_adapter: WebChatAdapter,
    auth_dep,
    config: Any,
) -> APIRouter:
    router = APIRouter(prefix="/chat")

    @router.websocket("/ws")
    async def chat_ws(websocket: WebSocket) -> None:
        # Auth check for WS: read token from query param or cookie
        secret = getattr(config.admin, "secret", "")
        if secret:
            from yuubot.admin.auth import verify_session_token
            token = (
                websocket.query_params.get("token", "")
                or websocket.cookies.get("yuu_admin_session", "")
            )
            if not verify_session_token(secret, token):
                await websocket.close(code=1008)
                return

        await websocket.accept()

        ctx_id = await _get_or_create_web_ctx()
        conversation_id = str(uuid.uuid4())
        queue: asyncio.Queue[str] = asyncio.Queue()
        web_adapter.bind_session(conversation_id, queue)

        async def _pump_replies() -> None:
            while True:
                msg = await queue.get()
                try:
                    await websocket.send_text(msg)
                except Exception:
                    break

        pump_task = asyncio.create_task(_pump_replies())

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if data.get("type") != "message":
                    continue
                text = str(data.get("text", "")).strip()
                if not text:
                    continue

                # Notify client that processing started
                await websocket.send_text(json.dumps({"type": "thinking"}))

                import yuullm
                from yuubot.daemon.actor import _workspace_root

                ws_root = _workspace_root(config, ctx_id)
                hm = HumanMessage(
                    ctx_id=ctx_id,
                    chat_type="web",
                    sender_id=0,
                    character_name=WEB_CHAR,
                    reply_target=conversation_id,
                    workspace_root=ws_root,
                    group_id=0,
                    bot_kind="master",
                    task_id="",
                    conversation_id=conversation_id,
                    content=yuullm.user(text),
                )
                asyncio.create_task(master_actor.handle_message(hm))

        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            web_adapter.unbind_session(conversation_id)

    return router
