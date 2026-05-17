"""Shared HTTP response helpers for daemon endpoints."""

from __future__ import annotations

from starlette.responses import JSONResponse


def error_response(reason: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "reason": reason},
        status_code=status_code,
    )
