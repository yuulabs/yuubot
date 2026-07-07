"""Admin WebSocket route."""

from __future__ import annotations

import asyncio

import msgspec
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ...app import Yuubot
from ...chat.listener import WsListener
from ..ws import handle_ws_command


def register_websocket_routes(api: FastAPI, app: Yuubot) -> None:
    @api.websocket("/api/ws")
    async def websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        connection_tasks: set[asyncio.Task[None]] = set()
        send_lock = asyncio.Lock()

        async def send(payload: dict[str, object]) -> None:
            async with send_lock:
                await websocket.send_text(msgspec.json.encode(payload).decode("utf-8"))

        ws_listener = WsListener(send)
        app.runtime.listeners.add(ws_listener)

        def track_task(task: asyncio.Task[None]) -> None:
            if task.get_name() == "conversation_send":
                return
            connection_tasks.add(task)
            task.add_done_callback(connection_tasks.discard)

        try:
            while True:
                raw = await websocket.receive_text()
                task = await handle_ws_command(app, raw, send, ws_listener)
                if task is not None:
                    track_task(task)
        except WebSocketDisconnect:
            pass
        finally:
            ws_listener.close()
            app.runtime.listeners.remove(ws_listener)
            for task in connection_tasks:
                task.cancel()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)
