"""Admin service runtime."""

from __future__ import annotations

from dataclasses import dataclass, field

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from yuubot.bootstrap.config import AdminConfig, BootstrapConfig
from yuubot.process import ASGIServer, UvicornServer, open_resources
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM


@dataclass
class DaemonClient:
    base_url: str


@dataclass
class AdminInfrastructure:
    asgi_server: ASGIServer = field(default_factory=UvicornServer)


@dataclass
class YuubotAdmin:
    """Running admin service."""

    config: AdminConfig
    resources: Resources
    daemon: DaemonClient
    asgi_server: ASGIServer

    async def close(self) -> None:
        await self.resources.close()

    def asgi_app(self) -> Starlette:
        return build_admin_asgi_app(
            config=self.config,
            resources=self.resources,
            daemon=self.daemon,
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

    return Starlette(routes=(Route("/healthz", health, methods=("GET",)),))


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
    )
