"""Public HTTP boundary: explicit whitelist without AdminAuth."""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from html import escape, escape as html_escape
from pathlib import Path
from urllib.parse import quote

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from ..app import Yuubot
from ..app.deployment import DeploymentConfig
from ..runtime.inbound import EnvSecretResolver, InboundBadRequestError, InboundUnauthorizedError
from ..runtime.shares import INDEX_CANDIDATES, ShareNotFoundError, share_content_type
from .client_ip import client_ip_from_scope
from .responses import error_response, json_response
from .errors import internal_error_detail, internal_error_message


class PublicWebhookRateLimiter:
    def __init__(
        self,
        limit: int = 60,
        window_s: float = 60.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.now = now
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, key: tuple[str, str]) -> bool:
        now = self.now()
        hits = self._hits[key]
        cutoff = now - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def create_public_app(app: Yuubot, deployment: DeploymentConfig | None = None) -> FastAPI:
    public = FastAPI()
    secrets = EnvSecretResolver()
    registry = app.runtime.integration_registry
    require_signature = deployment is not None and deployment.surface == "public"
    trusted_proxies = frozenset(deployment.trusted_proxies) if deployment is not None else frozenset()
    limiter = PublicWebhookRateLimiter()

    @public.get("/s/{share_id}")
    @public.get("/s/{share_id}/{path:path}")
    async def share_asset(share_id: str, path: str = "") -> Response:
        try:
            share_root, target = app.runtime.shares.resolve_path(share_id, path)
        except ShareNotFoundError:
            return error_response(404, "not_found", "share not found")
        if target.is_dir():
            index = _directory_index(target)
            if index is not None:
                return FileResponse(index, media_type=share_content_type(index))
            return Response(content=_directory_listing(share_id, path, target, share_root), media_type="text/html; charset=utf-8")
        if not target.is_file():
            return error_response(404, "not_found", "share not found")
        return FileResponse(target, media_type=share_content_type(target))

    @public.post("/webhooks/app/{integration_type}")
    async def app_webhook(integration_type: str, request: Request) -> object:
        client_ip = client_ip_from_scope(request.scope, trusted_proxies)
        if not limiter.allow((client_ip, integration_type)):
            return error_response(429, "rate_limited", "webhook rate limit exceeded")
        if integration_type not in registry.specs():
            return error_response(404, "not_found", f"integration type not found: {integration_type}")
        if not app.integration_enabled(integration_type):
            return error_response(503, "provider_unavailable", f"integration is not enabled: {integration_type}")

        adapter = registry.inbound_adapter(integration_type)
        try:
            envelope = await adapter.validate_webhook(request, secrets, require_signature)
        except InboundUnauthorizedError as exc:
            return error_response(401, "unauthorized", str(exc))
        except InboundBadRequestError as exc:
            return error_response(400, "bad_request", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return error_response(400, "bad_request", str(exc))

        try:
            result = await app.deliver_app_webhook(integration_type, envelope)
        except Exception as exc:
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, app.runtime.development),
                internal_error_detail(exc, app.runtime.development),
            )
        return json_response(result)

    @public.get("/api/mcp-oauth/{attempt_id}/callback", response_class=HTMLResponse)
    async def api_mcp_oauth_callback(
        attempt_id: str,
        code: str = "",
        state: str | None = None,
        error: str = "",
        token: str = "",
    ) -> Response:
        if error:
            if attempt_id in app.runtime.auth_attempts:
                await app.update_auth_attempt(attempt_id, status="failed", error=error)
            return HTMLResponse("<html><body><h1>Authorization failed</h1><p>You can close this tab.</p></body></html>", status_code=400)
        try:
            await app.complete_mcp_oauth_callback(attempt_id, code, state, token)
        except KeyError:
            return HTMLResponse("<html><body><h1>Authorization attempt not found</h1><p>You can close this tab.</p></body></html>", status_code=404)
        except ValueError as exc:
            return HTMLResponse(f"<html><body><h1>Authorization failed</h1><p>{html_escape(str(exc))}</p></body></html>", status_code=400)
        return HTMLResponse("<html><body><h1>Authorization received</h1><p>You can close this tab and return to yuubot.</p></body></html>")

    @public.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def public_not_found(path: str) -> object:
        del path
        return error_response(404, "not_found", "resource not found")

    return public


def _directory_index(directory: Path) -> Path | None:
    for name in INDEX_CANDIDATES:
        index = directory / name
        if index.is_file():
            return index
    return None


def _directory_listing(share_id: str, current_path: str, directory: Path, share_root: Path) -> str:
    current = _relative_display_path(current_path)
    rows: list[str] = []
    if current:
        rows.append(f'<li><a href="{_share_url(share_id, _parent_path(current), True)}">../</a></li>')
    for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        name = f"{child.name}/" if child.is_dir() else child.name
        rel_path = child.relative_to(share_root).as_posix()
        rows.append(f'<li><a href="{_share_url(share_id, rel_path, child.is_dir())}">{escape(name)}</a></li>')
    title = f"Index of /{escape(current)}"
    body = "\n".join(rows) if rows else "<li><em>empty directory</em></li>"
    return (
        "<!doctype html>"
        "<html><head>"
        '<meta charset="utf-8">'
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;}"
        "a{color:inherit}li{line-height:1.8}</style>"
        "</head><body>"
        f"<h1>{title}</h1><ul>{body}</ul>"
        "</body></html>"
    )


def _relative_display_path(path: str) -> str:
    return "/".join(part for part in path.strip("/").split("/") if part)


def _parent_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    parts.pop()
    return "/".join(parts)


def _share_url(share_id: str, rel_path: str, is_dir: bool) -> str:
    encoded = "/".join(quote(part) for part in rel_path.split("/") if part)
    suffix = "/" if is_dir else ""
    return f"/s/{quote(share_id)}/{encoded}{suffix}" if encoded else f"/s/{quote(share_id)}/"
