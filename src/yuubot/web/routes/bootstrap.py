"""Bootstrap and runtime snapshot routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...app.deployment import DeploymentConfig
from ..auth import AuthContext
from ..responses import error_response, json_response


def register_bootstrap_routes(api: FastAPI, app: Yuubot, deployment: DeploymentConfig) -> None:
    @api.get("/api/bootstrap")
    async def api_bootstrap(request: Request) -> Response:
        payload = msgspec.to_builtins(await app.bootstrap_snapshot())
        if isinstance(payload, dict):
            state = request.scope.get("state")
            auth = state.get("auth") if isinstance(state, dict) else None
            payload["auth"] = {
                "surface": deployment.surface,
                "mode": "none" if deployment.surface == "local_admin" else deployment.admin_auth.mode,
                "method": auth.auth_method if isinstance(auth, AuthContext) else None,
                "csrf_header": deployment.admin_auth.builtin.csrf_header,
            }
            payload["public_url_base"] = deployment.public_url_base
        return json_response(payload)

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
