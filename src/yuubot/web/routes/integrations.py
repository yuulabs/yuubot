"""Integration configuration routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...integrations import IntegrationRecord
from ...util.secrets import merge_redacted_config
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_integration_routes(api: FastAPI, app: Yuubot) -> None:
    @api.put("/api/integrations/{integration_type}/config")
    async def api_configure_integration(integration_type: str, request: Request) -> Response:
        if integration_type not in app.runtime.integration_registry.specs():
            return error_response(404, "not_found", "integration type not found")
        try:
            raw = await read_json(request, dict[str, object])
            name_value = raw.get("name", integration_type)
            config_value = raw.get("config", {})
            if not isinstance(name_value, str) or not isinstance(config_value, dict):
                raise ValueError("name must be a string and config must be an object")
            existing = app.integration_records.get(integration_type)
            await app.configure_integration(
                IntegrationRecord(
                    id=integration_type,
                    type=integration_type,
                    name=name_value,
                    config=merge_redacted_config(dict(config_value), existing.config if existing else None),
                )
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/integrations/{integration_type}/enable")
    async def api_enable_integration(integration_type: str) -> Response:
        try:
            integration = await app.enable_configured_integration(integration_type)
        except (KeyError, msgspec.ValidationError, ValueError) as exc:
            return error_response(422, "configuration_required", str(exc))
        if integration is None:
            return error_response(422, "configuration_required", "integration config is required before enable")
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/integrations/{integration_type}/disable")
    async def api_disable_integration(integration_type: str) -> Response:
        if not await app.disable_integration(integration_type):
            return error_response(404, "not_found", "integration config not found")
        return json_response(await app.bootstrap_snapshot())
