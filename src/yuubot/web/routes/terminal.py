"""Admin terminal WebSocket routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ...app import Yuubot
from ...runtime.terminal import TerminalSession


def register_terminal_routes(api: FastAPI, app: Yuubot) -> None:
    @api.websocket("/api/terminal/ws")
    async def terminal_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        auth = websocket.scope.get("state", {}).get("auth") if isinstance(websocket.scope.get("state"), dict) else None
        auth_user = getattr(auth, "user_id", "admin")
        session: TerminalSession | None = None

        async def send(payload: dict[str, object]) -> None:
            await websocket.send_text(msgspec.json.encode(payload).decode("utf-8"))

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    command = msgspec.json.decode(raw.encode(), type=dict[str, object])
                    command_type = command.get("type")
                    payload = command.get("payload", {})
                    if not isinstance(command_type, str) or not isinstance(payload, dict):
                        raise ValueError("terminal command requires type and object payload")
                    if command_type == "terminal.open":
                        if session is not None:
                            raise ValueError("terminal session is already open")
                        session = TerminalSession(
                            send=send,
                            auth_user=str(auth_user),
                            command=_terminal_str(payload.get("command")),
                            cwd=_terminal_str(payload.get("cwd")) or "~",
                            rows=_terminal_int(payload.get("rows"), 24),
                            cols=_terminal_int(payload.get("cols"), 80),
                        )
                        app.runtime.emit("terminal.opened", auth_user=str(auth_user), cwd=session.cwd, command=session.command)
                        await session.start()
                        continue
                    if session is None:
                        raise ValueError("terminal session is not open")
                    if command_type == "terminal.input":
                        await session.write(_terminal_str(payload.get("data")))
                    elif command_type == "terminal.resize":
                        await session.resize(rows=_terminal_int(payload.get("rows"), 24), cols=_terminal_int(payload.get("cols"), 80))
                    elif command_type == "terminal.close":
                        await session.close()
                        app.runtime.emit("terminal.closed", auth_user=str(auth_user))
                        session = None
                    else:
                        raise ValueError(f"unknown terminal command: {command_type}")
                except (msgspec.DecodeError, msgspec.ValidationError, ValueError, RuntimeError) as exc:
                    await send({"type": "terminal.error", "payload": {"message": str(exc)}})
        except WebSocketDisconnect:
            pass
        finally:
            if session is not None:
                await session.close()


def _terminal_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _terminal_int(value: object, default: int) -> int:
    return value if isinstance(value, int) else default
