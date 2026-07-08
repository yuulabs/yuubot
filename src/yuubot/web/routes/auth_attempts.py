"""Auth attempt admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...runtime.auth_attempts import AuthAttemptCreate
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import AuthAttemptUpdateBody


def register_auth_attempt_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/auth-attempts")
    async def api_auth_attempts() -> Response:
        return json_response({"items": app.auth_attempt_snapshots()})

    @api.post("/api/auth-attempts")
    async def api_create_auth_attempt(request: Request) -> Response:
        try:
            body = await read_json(request, AuthAttemptCreate)
            attempt = await app.create_auth_attempt(body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(attempt, 201)

    @api.put("/api/auth-attempts/{attempt_id}")
    async def api_update_auth_attempt(attempt_id: str, request: Request) -> Response:
        if attempt_id not in app.runtime.auth_attempts:
            return error_response(404, "not_found", "auth attempt not found")
        try:
            body = await read_json(request, AuthAttemptUpdateBody)
            attempt = await app.update_auth_attempt(
                attempt_id,
                status=body.status,
                error=body.error,
                action=body.action,
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(attempt)

    @api.delete("/api/auth-attempts/{attempt_id}")
    async def api_delete_auth_attempt(attempt_id: str) -> Response:
        if not await app.delete_auth_attempt(attempt_id):
            return error_response(404, "not_found", "auth attempt not found")
        return json_response({"id": attempt_id, "deleted": True})
