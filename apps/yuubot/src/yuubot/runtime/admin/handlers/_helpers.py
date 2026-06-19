"""Stateless utility helpers for admin HTTP handlers.

Pure helpers for body parsing, error responses, and daemon
response inspection. No side effects, no statefulness.
"""

from __future__ import annotations

import json
from typing import cast

from starlette.requests import Request
from starlette.responses import JSONResponse

from ._types import DaemonResponse


def _error(code: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "code": code, "detail": detail},
        status_code=status_code,
    )


async def _json_body(request: Request) -> dict[str, object] | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _error("validation_error", "invalid JSON body", 400)
    if not isinstance(payload, dict):
        return _error("validation_error", "body must be a JSON object", 400)
    return cast(dict[str, object], payload)


async def _optional_json_body(request: Request) -> dict[str, object] | JSONResponse:
    body = await request.body()
    if not body:
        return {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _error("validation_error", "invalid JSON body", 400)
    if not isinstance(payload, dict):
        return _error("validation_error", "body must be a JSON object", 400)
    return cast(dict[str, object], payload)


def _string_payload_value(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    return value.strip() if isinstance(value, str) else ""


def _daemon_json_body(response: DaemonResponse) -> dict[str, object]:
    try:
        body = json.loads(response.body.decode(errors="replace"))
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _daemon_error_warning(
    response: DaemonResponse,
    body: dict[str, object],
) -> str:
    detail = body.get("detail")
    if isinstance(detail, str) and detail:
        return f"daemon integration request failed: HTTP {response.status_code}: {detail}"
    return f"daemon integration request failed: HTTP {response.status_code}"
