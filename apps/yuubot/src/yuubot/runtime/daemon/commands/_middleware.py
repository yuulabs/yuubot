"""Secret-based authentication middleware for the commands sub-application."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from yuubot.runtime.daemon.commands._helpers import _error


class SecretMiddleware(BaseHTTPMiddleware):
    """Middleware that checks X-Daemon-Secret header against a configured secret."""

    def __init__(self, app, *, secret: str):
        super().__init__(app)
        self.secret = secret

    async def dispatch(self, request: Request, call_next):
        if not self.secret:
            return _error("misconfigured", "daemon_secret not set", 500)
        if request.headers.get("x-daemon-secret") != self.secret:
            return _error("unauthorized", "X-Daemon-Secret missing or invalid", 403)
        return await call_next(request)
