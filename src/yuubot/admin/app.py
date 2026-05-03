"""Admin panel FastAPI app — terminal, file browser, config API, and chat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from yuubot.admin.auth import clear_session_cookie, require_auth, set_session_cookie
from yuubot.admin.files import router as files_router
from yuubot.admin.terminal import handle_terminal

_STATIC_DIR = Path(__file__).parent / "static"


def create_admin_app(config: Any = None, master_actor: Any = None, web_adapter: Any = None) -> FastAPI:
    app = FastAPI(title="yuubot-admin", docs_url=None, redoc_url=None)

    secret = getattr(getattr(config, "admin", None), "secret", "") if config else ""
    auth_dep = require_auth(secret)

    app.include_router(files_router)

    # Config API
    if config is not None:
        from yuubot.admin.config_api import create_config_router
        app.include_router(create_config_router(config, auth_dep))

    # Chat WebSocket
    if master_actor is not None and config is not None and web_adapter is not None:
        from yuubot.admin.chat import create_chat_router

        app.include_router(create_chat_router(master_actor, web_adapter, auth_dep, config))

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/admin/settings")
    async def admin_settings(_: None = Depends(auth_dep)) -> JSONResponse:
        return JSONResponse(_admin_settings(config))

    @app.post("/auth/login")
    async def login(request: Request, response: Response) -> JSONResponse:
        if not secret:
            return JSONResponse({"ok": True})
        body = await request.json()
        if body.get("secret") != secret:
            return JSONResponse({"ok": False}, status_code=401)
        set_session_cookie(response, secret)
        return JSONResponse({"ok": True})

    @app.post("/auth/logout")
    async def logout(response: Response) -> JSONResponse:
        clear_session_cookie(response)
        return JSONResponse({"ok": True})

    @app.get("/auth/check")
    async def auth_check(_: None = Depends(auth_dep)) -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.websocket("/terminal/ws")
    async def terminal_ws(websocket: WebSocket) -> None:
        await handle_terminal(websocket)

    return app


def _admin_settings(config: Any = None) -> dict[str, Any]:
    yuutrace_cfg = {}
    if config is not None:
        raw = getattr(config, "yuuagents", {}).get("yuutrace", {})
        if isinstance(raw, dict):
            yuutrace_cfg = raw
    monitor_url = str(
        yuutrace_cfg.get("ui_url") or yuutrace_cfg.get("url") or ""
    ).strip()
    monitor_port = _coerce_port(
        yuutrace_cfg.get("ui_port"),
        _coerce_port(getattr(getattr(config, "docker", None), "traces_ui_port", None), 8782),
    )
    return {"monitor_url": monitor_url, "monitor_port": monitor_port}


def _coerce_port(value: Any, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 0 < port < 65536 else default
