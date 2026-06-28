"""Daemon proxy/forward handler factories.

Handler factories that forward admin HTTP requests
transparently to the daemon process — resource CRUD
and conversation endpoints.
"""

from __future__ import annotations

from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import Response

from ._daemon import (
    _request_daemon,
    _stream_daemon_sse,
    resource_proxy_path,
)
from ._types import DaemonClient, RequestDaemonFn


def make_proxy_daemon_resource_handler(
    *,
    daemon: DaemonClient,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon

    async def proxy_daemon_resource(request: Request) -> Response:
        body = await request.body()
        response = await _req(
            daemon,
            resource_proxy_path(request),
            method=request.method,
            body=body,
            content_type=request.headers.get("content-type", "application/json"),
        )
        return Response(
            response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

    return proxy_daemon_resource


def make_proxy_daemon_conversations_handler(
    *,
    daemon: DaemonClient,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon

    async def proxy_daemon_conversations(request: Request) -> Response:
        # SSE events endpoint must be streamed, not buffered
        if request.method == "GET" and request.path_params.get("path", "").endswith("/events"):
            return await _stream_daemon_sse(daemon, request.path_params["path"])
        body = await request.body()
        daemon_path = "/api/admin/conversations"
        path = request.path_params.get("path")
        if path:
            daemon_path += "/" + path
        if request.query_params:
            daemon_path += "?" + urlencode(tuple(request.query_params.multi_items()))
        response = await _req(
            daemon,
            daemon_path,
            method=request.method,
            body=body,
            content_type=request.headers.get("content-type", "application/json"),
        )
        return Response(
            response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

    return proxy_daemon_conversations


def make_proxy_daemon_actor_skills_handler(
    *,
    daemon: DaemonClient,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon

    async def proxy_daemon_actor_skills(request: Request) -> Response:
        body = await request.body()
        actor_id = request.path_params["actor_id"]
        daemon_path = f"/api/actors/{actor_id}/skills"
        path = request.path_params.get("path")
        if path:
            daemon_path += "/" + path
        response = await _req(
            daemon,
            daemon_path,
            method=request.method,
            body=body,
            content_type=request.headers.get("content-type", "application/json"),
        )
        return Response(
            response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

    return proxy_daemon_actor_skills
