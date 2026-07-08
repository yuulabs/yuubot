"""AdminAuth: proxy, builtin session, and loopback bypass."""

import secrets
import time
from collections.abc import Callable
from typing import Literal
from urllib.parse import quote

import msgspec
from attrs import define, field
from starlette.types import ASGIApp, Receive, Scope, Send

from ..app.deployment import AdminAuthConfig, DeploymentConfig
from ..util.asyncio_ import BackgroundSweeper
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
    created_at: float
    last_seen_at: float


@define
class SessionStore:
    _sessions: dict[str, BuiltinSession] = field(factory=dict)
    ttl_seconds: int = 7 * 24 * 60 * 60
    _now: Callable[[], float] = field(default=time.time, alias="now")
    _sweeper: BackgroundSweeper = field(factory=BackgroundSweeper, init=False)

    async def start_background_cleanup(self, interval_s: float = 300.0) -> None:
        await self._sweeper.start(interval_s, self.sweep_expired)

    async def stop_background_cleanup(self) -> None:
        await self._sweeper.stop()

    async def sweep_expired(self) -> None:
        self.prune_expired()

    def prune_expired(self) -> int:
        now = self._time()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if self._is_expired(session, now)
        ]
        for session_id in expired:
            self.delete(session_id)
        return len(expired)

    def create(self, user_id: str, display_name: str | None) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = self._time()
        self._sessions[session_id] = BuiltinSession(
            user_id,
            display_name,
            csrf_token,
            now,
            now,
        )
        return session_id, csrf_token

    def get(self, session_id: str) -> BuiltinSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._is_expired(session, self._time()):
            self.delete(session_id)
            return None
        return session

    def touch(self, session_id: str) -> None:
        session = self.get(session_id)
        if session is not None:
            session.last_seen_at = self._time()

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _time(self) -> float:
        return float(self._now())

    def _is_expired(self, session: BuiltinSession, now: float) -> bool:
        return now - session.last_seen_at > self.ttl_seconds


def is_auth_exempt(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    method = scope.get("method")
    path = scope.get("path")
    return (method == "POST" and path == "/api/auth/login") or (method == "GET" and path == "/login")


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
    auth = deployment.admin_auth

    if auth.mode == "builtin" and is_auth_exempt(scope):
        return AuthContext("login", auth_method="builtin_session")

    if auth.mode == "proxy":
        return _proxy_auth(scope, auth)

    if auth.mode == "builtin":
        return _builtin_auth(scope, auth, sessions)

    if auth.mode == "loopback_bypass":
        trusted_proxies = frozenset(deployment.trusted_proxies)
        if is_loopback(client_ip_from_scope(scope, trusted_proxies)):
            return AuthContext("local-admin", auth_method="loopback_bypass")

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
    return AuthContext(user_id, groups=groups, auth_method="proxy")


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
        session.user_id,
        session.display_name,
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
                if session is not None:
                    self.sessions.touch(session_id)

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
    if _wants_login_redirect(scope):
        location = _login_redirect_location(scope)
        await _send_response(scope, receive, send, 303, b"", None, [(b"location", location.encode("latin-1"))])
        return
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
    extra_headers: list[tuple[bytes, bytes]] | None = None,
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
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _wants_login_redirect(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    if scope.get("method") != "GET":
        return False
    path = scope.get("path")
    if not isinstance(path, str) or path.startswith("/api/"):
        return False
    headers = scope_headers(scope)
    accept = header_value(headers, "accept") or ""
    return "text/html" in accept or "*/*" in accept


def _login_redirect_location(scope: Scope) -> str:
    path = scope.get("path")
    query = scope.get("query_string")
    target = path if isinstance(path, str) and path else "/"
    if isinstance(query, bytes) and query:
        target = f"{target}?{query.decode('latin-1')}"
    if target == "/login":
        return "/login"
    return f"/login?redirect={quote(target, safe='')}"
