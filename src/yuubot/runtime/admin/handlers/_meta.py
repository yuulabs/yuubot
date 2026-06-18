"""Meta/health/SPA handler factories.

Top-level admin handlers: health check, integration kind
metadata, secret reveal, and single-page app fallback.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from yuubot.bootstrap.config import AdminConfig
from yuubot.core.integrations import IntegrationFactoryRegistry
from yuubot.core.secrets import Secret, secret_field_names
from yuubot.core.tools import ToolRegistry
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM
from yuubot.runtime.plugin_manager import ExternalPluginManager

from ._types import DaemonClient


def make_admin_health_handler(
    *,
    config: AdminConfig,
    resources: Resources,
    daemon: DaemonClient,
    plugin_manager: ExternalPluginManager,
):
    async def health(_: Request) -> JSONResponse:
        ingress_rules = await resources.repository.list(ActorIngressRuleORM)
        integrations = await resources.repository.list(IntegrationORM)
        return JSONResponse(
            {
                "status": "ok",
                "admin": f"{config.host}:{config.port}",
                "daemon": daemon.base_url,
                "ingress_rules": len(ingress_rules),
                "integrations": len(integrations),
                "plugins": len(plugin_manager.installed_manifests()),
            }
        )

    return health


def make_integration_kinds_handler(
    *,
    integration_factories: IntegrationFactoryRegistry,
):
    async def integration_kinds(_: Request) -> JSONResponse:
        kinds = [
            {
                "name": kind.name,
                "description": kind.description,
                "config_schema": kind.config_schema,
                "source_path_convention": kind.source_path_convention,
                "capabilities": [
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "description": spec.description,
                        "namespace": spec.namespace,
                    }
                    for spec in kind.capabilities
                ],
            }
            for kind in integration_factories.integration_kinds()
        ]
        return JSONResponse({"kinds": kinds})

    return integration_kinds


def make_tool_kinds_handler(
    *,
    tool_factories: ToolRegistry,
):
    async def tool_kinds(_: Request) -> JSONResponse:
        kinds = [
            {
                "name": kind.name,
                "description": kind.description,
                "config_schema": kind.config_schema,
            }
            for kind in tool_factories.tool_kinds()
        ]
        return JSONResponse({"kinds": kinds})

    return tool_kinds


def make_reveal_integration_secret_handler(
    *,
    resources: Resources,
    integration_factories: IntegrationFactoryRegistry,
):
    async def reveal_integration_secret(request: Request) -> JSONResponse:
        integration_id = request.path_params["id"]
        field = request.path_params["field"]

        record = await resources.repository.get(IntegrationORM, integration_id)
        if record is None:
            return JSONResponse(
                {"status": "error", "code": "not_found", "detail": "integration not found"},
                status_code=404,
            )
        try:
            factory = integration_factories.get(record.name)
        except LookupError as exc:
            return JSONResponse(
                {"status": "error", "code": "validation_error", "detail": str(exc)},
                status_code=400,
            )

        config_schema = factory.config_schema
        if field not in secret_field_names(config_schema):
            return JSONResponse(
                {"status": "error", "code": "not_found", "detail": "secret field not found"},
                status_code=404,
            )

        value = record.config.get(field)
        if not isinstance(value, Secret):
            return JSONResponse(
                {"status": "error", "code": "not_found", "detail": "secret not set"},
                status_code=404,
            )
        return JSONResponse({"status": "ok", "data": {"value": value.reveal()}})

    return reveal_integration_secret


def make_serve_spa_handler(
    *,
    index_path: Path,
):
    async def serve_spa(request: Request) -> FileResponse:
        """Serve index.html for client-side routing (SPA fallback).

        Returns index.html for any unmatched GET path. The catch-all route
        is appended last so API routes are checked first.
        """
        return FileResponse(
            str(index_path),
            headers={"Cache-Control": "no-cache"},
        )

    return serve_spa
