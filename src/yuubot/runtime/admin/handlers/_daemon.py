"""Daemon HTTP client and proxy utilities.

Low-level functions for communicating with the daemon process
over HTTP — request sending, SSE streaming, and resource path
construction for proxy passthrough.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse

from ._types import DaemonClient, DaemonResponse


def resource_proxy_path(request: Request) -> str:
    path = "/api/resources/" + request.path_params["resource_type"]
    row_id = request.path_params.get("id")
    action = request.path_params.get("action")
    if row_id is not None:
        path += f"/{row_id}"
    if action is not None:
        path += f"/{action}"
    if request.query_params:
        path += "?" + urlencode(tuple(request.query_params.multi_items()))
    return path


async def _stream_daemon_sse(daemon: DaemonClient, path: str) -> StreamingResponse:
    """Proxy daemon SSE events endpoint with streaming."""
    daemon_url = daemon.base_url.rstrip("/") + "/api/admin/conversations/" + path

    async def event_stream():
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "GET",
                    daemon_url,
                    headers={
                        "X-Daemon-Secret": daemon.daemon_secret,
                        "Accept": "text/event-stream",
                    },
                    timeout=httpx.Timeout(600.0, connect=10.0),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        yield f"event: error\ndata: {body.decode(errors='replace')}\n\n"
                        return
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk.decode(errors="replace")
            except httpx.RequestError:
                # daemon connection ended or went idle — stream ended, not an error
                return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _request_daemon(
    daemon: DaemonClient,
    path: str,
    *,
    method: str,
    body: bytes = b"",
    content_type: str = "application/json",
) -> DaemonResponse:
    if not daemon.daemon_secret:
        payload = json.dumps(
            {
                "status": "error",
                "code": "misconfigured",
                "detail": "daemon_secret not set",
            },
            ensure_ascii=True,
        ).encode()
        return DaemonResponse(status_code=500, body=payload)
    return await asyncio.to_thread(
        _send_daemon_request,
        daemon,
        path,
        method=method,
        body=body,
        content_type=content_type,
    )


def _send_daemon_request(
    daemon: DaemonClient,
    path: str,
    *,
    method: str,
    body: bytes,
    content_type: str,
) -> DaemonResponse:
    try:
        response = httpx.Client(timeout=httpx.Timeout(10.0)).request(
            method,
            daemon.base_url.rstrip("/") + path,
            content=body or None,
            headers={
                "Content-Type": content_type,
                "X-Daemon-Secret": daemon.daemon_secret,
            },
        )
        return DaemonResponse(
            status_code=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type", "application/json"),
        )
    except httpx.HTTPStatusError as exc:
        return DaemonResponse(
            status_code=exc.response.status_code,
            body=exc.response.content,
            content_type=exc.response.headers.get("content-type", "application/json"),
        )
    except httpx.RequestError as exc:
        payload = json.dumps(
            {
                "status": "error",
                "code": "daemon_unavailable",
                "detail": str(exc),
            },
            ensure_ascii=True,
        ).encode()
        return DaemonResponse(status_code=502, body=payload)
