"""Message route table admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...domain.records import RouteBody, RouteInput
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from ._helpers import route_exists


def register_route_table_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/routes")
    async def api_routes() -> Response:
        return json_response({"items": [msgspec.to_builtins(record) for record in await app.list_routes()]})

    @api.post("/api/routes")
    async def api_create_route(request: Request) -> Response:
        try:
            body = await read_json(request, RouteBody)
            record = body.to_record()
            if body.id and await route_exists(app, body.id):
                return error_response(409, "conflict", f"route already exists: {body.id}")
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        if record.actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.put_route(record)
        return json_response(msgspec.to_builtins(record))

    @api.put("/api/routes/{route_id}")
    async def api_put_route(route_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, RouteInput)
            record = body.to_record(route_id)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        if record.actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.put_route(record)
        return json_response(msgspec.to_builtins(record))

    @api.delete("/api/routes/{route_id}")
    async def api_delete_route(route_id: str) -> Response:
        if not await app.delete_route(route_id):
            return error_response(404, "not_found", "route not found")
        return json_response({"id": route_id, "deleted": True})
