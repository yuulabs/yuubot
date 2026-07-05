"""Public HTTP boundary: explicit whitelist without AdminAuth."""

from html import escape
from pathlib import Path
from urllib.parse import quote

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ..app import Yuubot
from ..runtime.inbound import EnvSecretResolver, InboundBadRequestError, InboundUnauthorizedError
from ..runtime.shares import INDEX_CANDIDATES, ShareNotFoundError, share_content_type
from .responses import error_response, json_response
from .errors import internal_error_detail, internal_error_message


def create_public_app(app: Yuubot) -> FastAPI:
    public = FastAPI()
    secrets = EnvSecretResolver()
    registry = app.runtime.integration_registry

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
                return Response(content=index.read_bytes(), media_type=share_content_type(index))
            return Response(content=_directory_listing(share_id, path, target, share_root), media_type="text/html; charset=utf-8")
        if not target.is_file():
            return error_response(404, "not_found", "share not found")
        return Response(content=target.read_bytes(), media_type=share_content_type(target))

    @public.post("/webhooks/app/{integration_type}")
    async def app_webhook(integration_type: str, request: Request) -> object:
        if integration_type not in registry.specs():
            return error_response(404, "not_found", f"integration type not found: {integration_type}")
        if not app.integration_enabled(integration_type):
            return error_response(503, "provider_unavailable", f"integration is not enabled: {integration_type}")

        adapter = registry.inbound_adapter(integration_type)
        try:
            envelope = await adapter.validate_webhook(request, secrets=secrets)
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
                internal_error_message(exc, development=app.runtime.development),
                detail=internal_error_detail(exc, development=app.runtime.development),
            )
        return json_response(result)

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
        rows.append(f'<li><a href="{_share_url(share_id, _parent_path(current), is_dir=True)}">../</a></li>')
    for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        name = f"{child.name}/" if child.is_dir() else child.name
        rel_path = child.relative_to(share_root).as_posix()
        rows.append(f'<li><a href="{_share_url(share_id, rel_path, is_dir=child.is_dir())}">{escape(name)}</a></li>')
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


def _share_url(share_id: str, rel_path: str, *, is_dir: bool) -> str:
    encoded = "/".join(quote(part) for part in rel_path.split("/") if part)
    suffix = "/" if is_dir else ""
    return f"/s/{quote(share_id)}/{encoded}{suffix}" if encoded else f"/s/{quote(share_id)}/"
