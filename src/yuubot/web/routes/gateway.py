"""Gateway Endpoint and Alias admin routes."""

from __future__ import annotations

import msgspec
from datetime import UTC, datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...llm.gateway import AliasInput, EndpointInput
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_gateway_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/gateway")
    async def api_gateway() -> Response:
        return json_response(msgspec.to_builtins(app.gateway_status_snapshot()))

    @api.get("/api/usage")
    async def api_usage(range: str = "day") -> Response:
        durations = {
            "day": timedelta(days=1),
            "week": timedelta(days=7),
            "month": timedelta(days=30),
            "year": timedelta(days=365),
        }
        since = None if range == "total" else (datetime.now(UTC) - durations.get(range, durations["day"])).isoformat()
        return json_response(await app.runtime.state.usage_dashboard(since))

    @api.put("/api/gateway/endpoints/{endpoint_id}")
    async def api_put_endpoint(endpoint_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, EndpointInput)
            record = await app.put_gateway_endpoint(endpoint_id, body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(msgspec.to_builtins(record))

    @api.post("/api/gateway/endpoints/{endpoint_id}/refresh")
    async def api_refresh_endpoint(endpoint_id: str) -> Response:
        try:
            record = await app.refresh_gateway_endpoint(endpoint_id)
        except KeyError:
            return error_response(404, "not_found", "endpoint not found")
        return json_response(msgspec.to_builtins(record))

    @api.delete("/api/gateway/endpoints/{endpoint_id}")
    async def api_delete_endpoint(endpoint_id: str) -> Response:
        try:
            deleted = await app.delete_gateway_endpoint(endpoint_id)
        except ValueError as exc:
            return error_response(409, "gateway_endpoint_in_use", str(exc))
        if not deleted:
            return error_response(404, "not_found", "endpoint not found")
        return Response(status_code=204)

    @api.put("/api/gateway/aliases/{alias_id}")
    async def api_put_alias(alias_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, AliasInput)
            record = await app.put_gateway_alias(alias_id, body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(msgspec.to_builtins(record))

    @api.delete("/api/gateway/aliases/{alias_id}")
    async def api_delete_alias(alias_id: str) -> Response:
        try:
            deleted = await app.delete_gateway_alias(alias_id)
        except ValueError as exc:
            return error_response(409, "gateway_alias_in_use", str(exc))
        if not deleted:
            return error_response(404, "not_found", "alias not found")
        return Response(status_code=204)
