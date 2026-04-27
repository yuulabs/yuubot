"""Admin panel FastAPI app — terminal + health endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from yuubot.admin.terminal import handle_terminal

_STATIC_DIR = Path(__file__).parent / "static"


def create_admin_app() -> FastAPI:
    app = FastAPI(title="yuubot-admin", docs_url=None, redoc_url=None)

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.websocket("/terminal/ws")
    async def terminal_ws(websocket: WebSocket) -> None:
        await handle_terminal(websocket)

    return app
