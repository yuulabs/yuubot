"""Host and path dispatch between admin and public ASGI apps."""

import re

from starlette.types import ASGIApp, Receive, Scope, Send

from ..app.deployment import DeploymentConfig, hosts_for_url_base
from .responses import error_response

PUBLIC_PATHS = (
    re.compile(r"^/s/[^/]+(?:/.*)?$"),
    re.compile(r"^/webhooks/app/[^/]+$"),
)


def is_public_path(path: str) -> bool:
    return any(pattern.match(path) is not None for pattern in PUBLIC_PATHS)


def host_header(scope: Scope) -> str:
    headers = scope.get("headers")
    if not isinstance(headers, list):
        return ""
    for name, value in headers:
        if name == b"host" and isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("latin-1").lower()
    return ""


class BoundaryApp:
    def __init__(
        self,
        deployment: DeploymentConfig,
        admin_app: ASGIApp,
        public_app: ASGIApp,
    ) -> None:
        self.admin_hosts = hosts_for_url_base(deployment.admin_url_base)
        self.public_hosts = hosts_for_url_base(deployment.public_url_base)
        self.same_host = self.admin_hosts == self.public_hosts
        self.admin_app = admin_app
        self.public_app = public_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.admin_app(scope, receive, send)
            return

        path = scope.get("path")
        if not isinstance(path, str):
            await self._not_found(scope, receive, send)
            return

        host = host_header(scope)
        if self.same_host or host in self.admin_hosts:
            if is_public_path(path):
                await self.public_app(scope, receive, send)
                return
            await self.admin_app(scope, receive, send)
            return

        if host in self.public_hosts:
            if not is_public_path(path):
                await self._not_found(scope, receive, send)
                return
            await self.public_app(scope, receive, send)
            return

        await self._not_found(scope, receive, send)

    async def _not_found(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = error_response(404, "not_found", "resource not found")
        if scope["type"] == "websocket":
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message["type"] == "websocket.connect":
                    break
        headers = [(b"content-type", b"application/json")]
        await send({"type": "http.response.start", "status": response.status_code, "headers": headers})
        await send({"type": "http.response.body", "body": response.body})


def wrap_admin_auth(admin_app: ASGIApp, deployment: DeploymentConfig, sessions: object) -> ASGIApp:
    from .auth import AdminAuthMiddleware, SessionStore

    if not isinstance(sessions, SessionStore):
        raise TypeError("sessions must be a SessionStore")
    return AdminAuthMiddleware(admin_app, deployment, sessions)


def create_boundary_app(
    deployment: DeploymentConfig,
    admin_app: ASGIApp,
    public_app: ASGIApp,
    *,
    sessions: object,
    protect_admin: bool = True,
) -> ASGIApp:
    protected_admin = wrap_admin_auth(admin_app, deployment, sessions) if protect_admin else admin_app
    return BoundaryApp(deployment, protected_admin, public_app)
