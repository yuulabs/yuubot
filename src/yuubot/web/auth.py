"""AdminAuth: proxy, builtin session, and loopback bypass."""

import re
import secrets
from typing import Literal

import msgspec
from attrs import define, field
from starlette.types import ASGIApp, Receive, Scope, Send

from ..app.deployment import AdminAuthConfig, DeploymentConfig
from .client_ip import client_ip_from_scope, header_value, is_loopback
from .responses import error_response

AuthMethod = Literal["proxy", "builtin_session", "loopback_bypass"]


class AuthContext(msgspec.Struct, frozen=True):
    user_id: str
    display_name: str | None = None
    groups: tuple[str, ...] = ()
    auth_method: AuthMethod = "proxy"


class LoginBody(msgspec.Struct, frozen=True):
    password: str


@define
class BuiltinSession:
    user_id: str
    display_name: str | None
    csrf_token: str


@define
class SessionStore:
    _sessions: dict[str, BuiltinSession] = field(factory=dict)

    def create(self, *, user_id: str, display_name: str | None) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self._sessions[session_id] = BuiltinSession(user_id=user_id, display_name=display_name, csrf_token=csrf_token)
        return session_id, csrf_token

    def get(self, session_id: str) -> BuiltinSession | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


ACTOR_INBOUND_PATH = re.compile(r"^/api/actors/[^/]+/inbound$")


def is_actor_inbound(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    method = scope.get("method")
    path = scope.get("path")
    return method == "POST" and isinstance(path, str) and ACTOR_INBOUND_PATH.match(path) is not None


def is_auth_exempt(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    method = scope.get("method")
    path = scope.get("path")
    return method == "POST" and path == "/api/auth/login"


def scope_headers(scope: Scope) -> dict[bytes, bytes]:
    headers = scope.get("headers")
    if not isinstance(headers, list):
        return {}
    return {name: value for name, value in headers}


def authenticate_scope(
    scope: Scope,
    deployment: DeploymentConfig,
    sessions: SessionStore,
) -> AuthContext | None:
    trusted = frozenset(deployment.trusted_proxies)
    client_ip = client_ip_from_scope(scope, trusted)
    auth = deployment.admin_auth

    if auth.mode == "loopback_bypass" and is_loopback(client_ip):
        return AuthContext(user_id="loopback", auth_method="loopback_bypass")

    if is_actor_inbound(scope) and is_loopback(client_ip):
        return AuthContext(user_id="loopback-inbound", auth_method="loopback_bypass")

    if is_auth_exempt(scope):
        return AuthContext(user_id="login", auth_method="builtin_session")

    if auth.mode == "proxy":
        return _proxy_auth(scope, auth)

    if auth.mode == "builtin":
        return _builtin_auth(scope, auth, sessions)

    return None


def require_csrf(scope: Scope, deployment: DeploymentConfig, session: BuiltinSession) -> bool:
    method = scope.get("method")
    if method not in {"POST", "PUT", "DELETE"}:
        return True
    headers = scope_headers(scope)
    token = header_value(headers, deployment.admin_auth.builtin.csrf_header.lower())
    return token == session.csrf_token


def _proxy_auth(scope: Scope, auth: AdminAuthConfig) -> AuthContext | None:
    headers = scope_headers(scope)
    user_id = header_value(headers, auth.proxy.user_header.lower())
    if user_id is None:
        return None
    groups_header = auth.proxy.groups_header
    groups: tuple[str, ...] = ()
    if groups_header is not None:
        raw_groups = header_value(headers, groups_header.lower())
        if raw_groups is not None:
            groups = tuple(part.strip() for part in raw_groups.split(",") if part.strip())
    return AuthContext(user_id=user_id, groups=groups, auth_method="proxy")


def _builtin_auth(scope: Scope, auth: AdminAuthConfig, sessions: SessionStore) -> AuthContext | None:
    headers = scope_headers(scope)
    cookie_header = header_value(headers, "cookie")
    if cookie_header is None:
        return None
    session_value = _cookie_value(cookie_header, auth.builtin.session_cookie_name)
    if session_value is None:
        return None
    session = sessions.get(session_value)
    if session is None:
        return None
    return AuthContext(
        user_id=session.user_id,
        display_name=session.display_name,
        auth_method="builtin_session",
    )


def _cookie_value(cookie_header: str, name: str) -> str | None:
    prefix = f"{name}="
    for part in cookie_header.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix) :]
    return None


class AdminAuthMiddleware:
    def __init__(self, app: ASGIApp, deployment: DeploymentConfig, sessions: SessionStore) -> None:
        self.app = app
        self.deployment = deployment
        self.sessions = sessions

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        auth = authenticate_scope(scope, self.deployment, self.sessions)
        if auth is None:
            await _reject_unauthorized(scope, receive, send)
            return

        if scope["type"] == "http" and self.deployment.admin_auth.mode == "builtin":
            session_id = _session_id_from_scope(scope, self.deployment.admin_auth.builtin.session_cookie_name)
            if session_id is not None and not is_auth_exempt(scope):
                session = self.sessions.get(session_id)
                if session is not None and not require_csrf(scope, self.deployment, session):
                    await _reject_forbidden(scope, receive, send)
                    return

        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["auth"] = auth
        await self.app(scope, receive, send)


def _session_id_from_scope(scope: Scope, cookie_name: str) -> str | None:
    headers = scope_headers(scope)
    cookie_header = header_value(headers, "cookie")
    if cookie_header is None:
        return None
    return _cookie_value(cookie_header, cookie_name)


async def _reject_unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
    response = error_response(401, "unauthorized", "authentication required")
    body = bytes(response.body)
    await _send_response(scope, receive, send, response.status_code, body, response.media_type)


async def _reject_forbidden(scope: Scope, receive: Receive, send: Send) -> None:
    response = error_response(403, "forbidden", "csrf validation failed")
    body = bytes(response.body)
    await _send_response(scope, receive, send, response.status_code, body, response.media_type)


async def _send_response(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    body: bytes,
    media_type: str | None,
) -> None:
    if scope["type"] == "websocket":
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            if message["type"] == "websocket.connect":
                break
    headers: list[tuple[bytes, bytes]] = []
    if media_type is not None:
        headers.append((b"content-type", media_type.encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
