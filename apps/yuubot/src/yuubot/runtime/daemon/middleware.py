"""Daemon secret authentication middleware.

Consolidates the 8 repeated X-Daemon-Secret checks that were previously
inline in every route handler in app.py.
"""

from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from yuubot.runtime.http_utils import error_response


class DaemonSecretMiddleware(BaseHTTPMiddleware):
    """Enforce X-Daemon-Secret header for protected API routes.

    Not all daemon routes require the daemon secret — /healthz and
    /ingest use different auth mechanisms. The middleware guards
    administration endpoints.
    """

    _PROTECTED_PREFIXES: tuple[str, ...] = (
        "/api/status",
        "/api/admin/",
    )

    def __init__(self, app, *, secret: str) -> None:
        super().__init__(app)
        self._secret = secret

    async def dispatch(self, request: Request, call_next):
        if self._should_protect(request.url.path):
            error = self._check_secret(request)
            if error is not None:
                return self._error_for(request.url.path, error)
        return await call_next(request)

    # -- internal --

    def _should_protect(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._PROTECTED_PREFIXES)

    def _check_secret(self, request: Request) -> str | None:
        """Return an error message when the daemon secret is missing or invalid."""
        if not self._secret:
            return "server.daemon_secret is not configured"
        if request.headers.get("x-daemon-secret") != self._secret:
            return "X-Daemon-Secret is missing or invalid"
        return None

    @staticmethod
    def _error_for(path: str, reason: str) -> JSONResponse | StreamingResponse:
        """Return the appropriate error response for the request path.

        SSE streaming endpoints (paths ending in ``/events``) receive
        an ``event: error`` frame so that the client can display the
        failure.  All other endpoints get a plain JSON error body.
        """
        if path.endswith("/events"):
            payload = json.dumps(
                {"status": "error", "error": reason}, ensure_ascii=True
            )
            return StreamingResponse(
                iter((f"event: error\ndata: {payload}\n\n",)),
                status_code=403,
                media_type="text/event-stream",
            )
        return error_response(reason, status_code=403)
