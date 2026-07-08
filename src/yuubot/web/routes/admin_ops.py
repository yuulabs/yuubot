"""Loopback-only admin control routes."""

from __future__ import annotations

from collections.abc import Callable

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_admin_ops_routes(
    api: FastAPI,
    app: Yuubot,
    client_is_loopback: Callable[[Request], bool],
    on_shutdown: Callable[[], None] | None = None,
) -> None:
    @api.post("/api/admin/interrupt")
    async def admin_interrupt(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "admin requests require loopback access")
        try:
            raw = await read_json(request, dict[str, object])
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        if raw.get("all") is True:
            return json_response({"interrupted": app.interrupt_all()})
        conversation_id = raw.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            return error_response(400, "bad_request", "conversation_id is required")
        return json_response({"conversation_id": conversation_id, "interrupted": app.interrupt(conversation_id)})

    @api.post("/api/admin/shutdown")
    async def admin_shutdown(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "admin requests require loopback access")
        if on_shutdown is not None:
            on_shutdown()
        return json_response({"status": "shutting_down"})
