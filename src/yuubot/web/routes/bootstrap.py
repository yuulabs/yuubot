"""Bootstrap and runtime snapshot routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI
from fastapi.responses import Response

from ...app import Yuubot
from ..responses import error_response, json_response


def register_bootstrap_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/bootstrap")
    async def api_bootstrap() -> Response:
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/integrations")
    async def api_integrations() -> Response:
        return json_response({"items": await app.integration_snapshots()})

    @api.get("/api/integrations/{integration_type}")
    async def api_integration(integration_type: str) -> Response:
        for integration in await app.integration_snapshots():
            if integration.type == integration_type:
                return json_response(integration)
        return error_response(404, "not_found", "integration type not found")

    @api.get("/api/provider-protocols")
    async def api_provider_protocols() -> Response:
        return json_response({"items": [msgspec.to_builtins(item) for item in app.runtime.provider_registry.protocol_specs()]})

    @api.get("/api/runtime")
    async def api_runtime() -> Response:
        return json_response(app.runtime_snapshot())
