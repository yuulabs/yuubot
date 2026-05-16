"""Admin service runtime."""

from __future__ import annotations

from dataclasses import dataclass, field

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from yuubot.bootstrap.config import AdminConfig, BootstrapConfig
from yuubot.core.integrations import (
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.secrets import Secret, secret_field_names
from yuubot.process import ASGIServer, UvicornServer, open_resources
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM


@dataclass
class DaemonClient:
    base_url: str


@dataclass
class AdminInfrastructure:
    asgi_server: ASGIServer = field(default_factory=UvicornServer)
    integration_factories: IntegrationFactoryRegistry = field(
        default_factory=default_integration_factories
    )


@dataclass
class YuubotAdmin:
    """Running admin service."""

    config: AdminConfig
    resources: Resources
    daemon: DaemonClient
    asgi_server: ASGIServer
    integration_factories: IntegrationFactoryRegistry

    async def close(self) -> None:
        await self.resources.close()

    def asgi_app(self) -> Starlette:
        return build_admin_asgi_app(
            config=self.config,
            resources=self.resources,
            daemon=self.daemon,
            integration_factories=self.integration_factories,
        )

    async def serve(self) -> None:
        try:
            await self.asgi_server.serve(
                self.asgi_app(),
                host=self.config.host,
                port=self.config.port,
            )
        finally:
            await self.resources.close()


def build_admin_asgi_app(
    *,
    config: AdminConfig,
    resources: Resources,
    daemon: DaemonClient,
    integration_factories: IntegrationFactoryRegistry,
) -> Starlette:
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
            }
        )

    async def integration_kinds(_: Request) -> JSONResponse:
        kinds = [
            {
                "name": kind.name,
                "description": kind.description,
                "config_schema": kind.config_schema,
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

        config_schema = getattr(factory, "config_schema", None)
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

    return Starlette(
        routes=(
            Route("/healthz", health, methods=("GET",)),
            Route(
                "/api/integration-kinds",
                integration_kinds,
                methods=("GET",),
            ),
            Route(
                "/api/integrations/{id}/secrets/{field}/reveal",
                reveal_integration_secret,
                methods=("GET",),
            ),
        )
    )


async def build_admin(
    config: BootstrapConfig,
    *,
    components: AdminInfrastructure | None = None,
) -> YuubotAdmin:
    config.validate()
    components = components or AdminInfrastructure()
    resources = await open_resources(config)
    daemon_url = f"http://{config.server.daemon_host}:{config.server.daemon_port}"

    return YuubotAdmin(
        config=config.admin,
        resources=resources,
        daemon=DaemonClient(base_url=daemon_url),
        asgi_server=components.asgi_server,
        integration_factories=components.integration_factories,
    )
