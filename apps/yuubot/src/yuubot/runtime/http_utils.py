"""Shared HTTP response helpers for daemon endpoints."""

from __future__ import annotations

from starlette.responses import JSONResponse


def error_response(
    reason: str,
    *,
    status_code: int,
    code: str = "error",
    hint: str = "",
) -> JSONResponse:
    body = {
        "status": "error",
        "code": code,
        "detail": reason,
        "reason": reason,
    }
    if hint:
        body["hint"] = hint
    return JSONResponse(
        body,
        status_code=status_code,
    )
