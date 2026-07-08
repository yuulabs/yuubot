"""Integration configuration routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...integrations import IntegrationRecord
from ...integrations.records import IntegrationConfigInput
from ...util.secrets import merge_redacted_config
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_integration_routes(api: FastAPI, app: Yuubot) -> None:
    @api.put("/api/integrations/{integration_type}/config")
    async def api_configure_integration(integration_type: str, request: Request) -> Response:
        if integration_type not in app.runtime.integration_registry.specs():
            return error_response(404, "not_found", "integration type not found")
        try:
            body = await read_json(request, IntegrationConfigInput)
            existing = app.integration_records.get(integration_type)
            await app.configure_integration(
                IntegrationRecord(
                    integration_type,
                    integration_type,
                    body.name or integration_type,
                    merge_redacted_config(dict(body.config), existing.config if existing else None),
                )
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        snapshot = await app.integration_snapshot(integration_type)
        assert snapshot is not None
        return json_response(msgspec.to_builtins(snapshot))

    @api.post("/api/integrations/{integration_type}/enable")
    async def api_enable_integration(integration_type: str) -> Response:
        try:
            integration = await app.enable_configured_integration(integration_type)
        except (KeyError, msgspec.ValidationError, ValueError) as exc:
            return error_response(422, "configuration_required", str(exc))
        if integration is None:
            return error_response(422, "configuration_required", "integration config is required before enable")
        snapshot = await app.integration_snapshot(integration_type)
        assert snapshot is not None
        return json_response(msgspec.to_builtins(snapshot))

    @api.post("/api/integrations/{integration_type}/disable")
    async def api_disable_integration(integration_type: str) -> Response:
        if not await app.disable_integration(integration_type):
            return error_response(404, "not_found", "integration config not found")
        snapshot = await app.integration_snapshot(integration_type)
        assert snapshot is not None
        return json_response(msgspec.to_builtins(snapshot))
