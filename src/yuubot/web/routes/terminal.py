"""Admin terminal WebSocket routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ...app import Yuubot
from ...runtime.event_payloads import TerminalClosedPayload, TerminalOpenedPayload
from ...runtime.terminal import TerminalSession
from ..auth import AuthContext
from ..terminal_commands import (
    TerminalCloseCommand,
    TerminalCommand,
    TerminalInputCommand,
    TerminalOpenCommand,
    TerminalResizeCommand,
)


def register_terminal_routes(api: FastAPI, app: Yuubot) -> None:
    @api.websocket("/api/terminal/ws")
    async def terminal_websocket(websocket: WebSocket) -> None:
        auth = _auth_context(websocket)
        if auth is None:
            await websocket.close(code=1008, reason="authentication required")
            return
        auth_user = auth.user_id
        await websocket.accept()
        session: TerminalSession | None = None

        async def send(payload: dict[str, object]) -> None:
            await websocket.send_text(msgspec.json.encode(payload).decode("utf-8"))

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    command = msgspec.json.decode(raw.encode(), type=TerminalCommand)
                    match command:
                        case TerminalOpenCommand(payload=payload):
                            if session is not None:
                                raise ValueError("terminal session is already open")
                            session = TerminalSession(
                                send,
                                auth_user,
                                payload.command,
                                payload.cwd or "~",
                                payload.rows,
                                payload.cols,
                            )
                            app.runtime.emit(
                                TerminalOpenedPayload(
                                    auth_user,
                                    session.cwd,
                                    session.command,
                                )
                            )
                            await session.start()
                        case TerminalInputCommand(payload=payload):
                            if session is None:
                                raise ValueError("terminal session is not open")
                            await session.write(payload.data)
                        case TerminalResizeCommand(payload=payload):
                            if session is None:
                                raise ValueError("terminal session is not open")
                            await session.resize(rows=payload.rows, cols=payload.cols)
                        case TerminalCloseCommand():
                            if session is None:
                                raise ValueError("terminal session is not open")
                            await session.close()
                            app.runtime.emit(TerminalClosedPayload(auth_user))
                            session = None
                except (msgspec.DecodeError, msgspec.ValidationError, ValueError, RuntimeError) as exc:
                    await send({"type": "terminal.error", "payload": {"message": str(exc)}})
        except WebSocketDisconnect:
            pass
        finally:
            if session is not None:
                await session.close()


def _auth_context(websocket: WebSocket) -> AuthContext | None:
    state = websocket.scope.get("state")
    if not isinstance(state, dict):
        return None
    auth = state.get("auth")
    return auth if isinstance(auth, AuthContext) else None
