"""WebUI server for trace visualization.

Implements ``ytrace ui`` -- serves the @yuutrace/ui pre-built static
assets alongside a REST API that queries trace data from SQLite.

Usage::

    ytrace ui --db ./traces.db --port 8080
"""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .db import get_blob, get_conversation, get_span, init_db, list_conversations

if TYPE_CHECKING:
    import uvicorn

logger = logging.getLogger("yuutrace.ui")


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------


async def _health(request: Request) -> JSONResponse:
    """GET /api/health"""
    return JSONResponse({"status": "ok"})


async def _list_conversations(request: Request) -> JSONResponse:
    """GET /api/conversations?limit=50&offset=0&agent=..."""
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))
    agent = request.query_params.get("agent")

    result = list_conversations(
        request.app.state.db,
        limit=limit,
        offset=offset,
        agent=agent or None,
    )
    return JSONResponse(result)


async def _get_conversation(request: Request) -> JSONResponse:
    """GET /api/conversations/{id}"""
    conversation_id = request.path_params["id"]
    result = get_conversation(request.app.state.db, conversation_id)
    if result is None:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)
    return JSONResponse(result)


async def _get_span(request: Request) -> JSONResponse:
    """GET /api/spans/{id}"""
    span_id = request.path_params["id"]
    result = get_span(request.app.state.db, span_id)
    if result is None:
        return JSONResponse({"error": "Span not found"}, status_code=404)
    return JSONResponse(result)


async def _get_blob(request: Request) -> Response:
    """GET /api/blobs/{sha256}"""
    sha256 = request.path_params["sha256"]
    result = get_blob(request.app.state.db, sha256)
    if result is None:
        return Response(status_code=404)
    media_type, data = result
    return Response(content=data, media_type=media_type)


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


def _resolve_static_dir() -> Path:
    """Locate the pre-built static assets directory.

    Uses ``importlib.resources`` so it works both in editable installs
    and from wheel/sdist.
    """
    try:
        ref = importlib.resources.files("yuutrace.cli._static")
        # as_posix() works for both Path and Traversable
        static_path = Path(str(ref))
        if static_path.is_dir():
            return static_path
    except (ModuleNotFoundError, TypeError):
        pass

    # Fallback: relative to this file
    fallback = Path(__file__).parent / "_static"
    return fallback


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


class _NoCacheHTMLMiddleware:
    """Set no-cache headers on HTML responses to prevent stale index.html.

    Hashed JS/CSS assets are safe to cache (the filename changes on rebuild),
    but index.html must always be revalidated so the browser fetches the
    correct asset references after a rebuild.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        is_html = path == "/" or path.endswith(".html")

        async def send_wrapper(message: dict) -> None:
            if is_html and message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(
                    (b"cache-control", b"no-cache, no-store, must-revalidate")
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _build_app(db_path: str) -> Starlette:
    """Create the Starlette ASGI application."""
    static_dir = _resolve_static_dir()
    logger.info("Static assets directory: %s", static_dir)

    # API routes come first (Starlette matches in order)
    routes: list[Route | Mount] = [
        Route("/api/health", _health, methods=["GET"]),
        Route("/api/conversations", _list_conversations, methods=["GET"]),
        Route("/api/conversations/{id:path}", _get_conversation, methods=["GET"]),
        Route("/api/spans/{id:path}", _get_span, methods=["GET"]),
        Route("/api/blobs/{sha256}", _get_blob, methods=["GET"]),
    ]

    # Mount static files last — html=True enables SPA fallback
    if static_dir.is_dir() and any(static_dir.iterdir()):
        routes.append(
            Mount("/", app=StaticFiles(directory=str(static_dir), html=True)),
        )
    else:
        logger.warning(
            "Static assets not found at %s. "
            "Run 'scripts/build_ui.sh' to build the frontend.",
            static_dir,
        )

    app = Starlette(
        routes=routes,
        middleware=[Middleware(_NoCacheHTMLMiddleware)],
    )
    app.state.db = init_db(db_path)
    return app


_server: "uvicorn.Server | None" = None


def run_ui(*, db_path: str, host: str, port: int) -> None:
    """Start the WebUI server.

    The server does two things:
    1. Provides REST API endpoints that query trace data from SQLite.
    2. Serves the pre-built static assets from @yuutrace/ui (TracePage).

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    host:
        Bind host for the HTTP server.
    port:
        Port for the HTTP server.
    """
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger.info("Starting yuutrace UI on port %d (db: %s)", port, db_path)

    app = _build_app(db_path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    global _server
    _server = uvicorn.Server(config)
    _server.run()


def shutdown_ui() -> None:
    """Signal the UI server to shut down gracefully."""
    if _server is not None:
        _server.should_exit = True
