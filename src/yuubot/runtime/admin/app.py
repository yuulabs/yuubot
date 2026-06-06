"""Admin service runtime.

Handler factories live in ``handlers.py``. This module assembles routes
and manages the ``YuubotAdmin`` lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from yuubot.bootstrap.config import AdminConfig, BootstrapConfig
from yuubot.bootstrap.layout import DataLayout
from yuubot.core.integrations import (
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.runtime.admin.handlers import (
    DaemonClient,
    DaemonResponse,
    _create_provider_model_client,
    _request_daemon,
    make_admin_health_handler,
    make_install_plugin_handler,
    make_integration_kinds_handler,
    make_list_plugins_handler,
    make_provider_models_handler,
    make_proxy_daemon_conversations_handler,
    make_proxy_daemon_resource_handler,
    make_reveal_integration_secret_handler,
    make_serve_spa_handler,
    make_uninstall_plugin_handler,
    make_validate_provider_handler,
)
from yuubot.resources.root import Resources
from yuubot.runtime.plugin_manager import (
    ExternalPluginFactoryLoader,
    ExternalPluginManager,
)
from yuubot.runtime.process import ASGIServer, UvicornServer, open_resources
from yuutrace.cli.ui import _build_app as build_trace_app

__all__ = [
    "AdminInfrastructure",
    "DaemonClient",
    "DaemonResponse",
    "YuubotAdmin",
    "build_admin",
    "build_admin_asgi_app",
]


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
    plugin_manager: ExternalPluginManager
    trace_db_path: str = ""

    async def close(self) -> None:
        await self.resources.close()

    def asgi_app(self) -> Starlette:
        return build_admin_asgi_app(
            config=self.config,
            resources=self.resources,
            daemon=self.daemon,
            integration_factories=self.integration_factories,
            plugin_manager=self.plugin_manager,
            trace_db_path=self.trace_db_path,
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
    plugin_manager: ExternalPluginManager | None = None,
    trace_db_path: str = "",
) -> Starlette:
    if plugin_manager is None:
        layout = DataLayout.from_path("~/.yuubot")
        plugin_manager = ExternalPluginManager(
            plugins_dir=layout.plugins_dir,
            data_root=layout.data_dir,
        )

    # Assemble handlers via explicit-dependency factories.
    routes: list[Route | Mount] = [
        Route(
            "/healthz",
            make_admin_health_handler(
                config=config,
                resources=resources,
                daemon=daemon,
                plugin_manager=plugin_manager,
            ),
            methods=("GET",),
        ),
        Route(
            "/api/resources/{resource_type}",
            make_proxy_daemon_resource_handler(
                daemon=daemon,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("GET", "POST"),
        ),
        Route(
            "/api/resources/{resource_type}/{id}",
            make_proxy_daemon_resource_handler(
                daemon=daemon,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("GET", "PUT", "DELETE"),
        ),
        Route(
            "/api/resources/{resource_type}/{id}/{action}",
            make_proxy_daemon_resource_handler(
                daemon=daemon,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("POST",),
        ),
        Route(
            "/api/admin/conversations",
            make_proxy_daemon_conversations_handler(
                daemon=daemon,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("GET", "POST"),
        ),
        Route(
            "/api/admin/conversations/{path:path}",
            make_proxy_daemon_conversations_handler(
                daemon=daemon,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("GET", "POST"),
        ),
        Route(
            "/api/integration-kinds",
            make_integration_kinds_handler(
                integration_factories=integration_factories,
            ),
            methods=("GET",),
        ),
        Route(
            "/api/integrations/{id}/secrets/{field}/reveal",
            make_reveal_integration_secret_handler(
                resources=resources,
                integration_factories=integration_factories,
            ),
            methods=("GET",),
        ),
        Route(
            "/api/providers/{id}/models",
            make_provider_models_handler(
                resources=resources,
                _create_provider_model_client_fn=_create_provider_model_client,
            ),
            methods=("POST",),
        ),
        Route(
            "/api/providers/{id}/validate",
            make_validate_provider_handler(
                resources=resources,
                _create_provider_model_client_fn=_create_provider_model_client,
            ),
            methods=("POST",),
        ),
        Route(
            "/api/plugins",
            make_list_plugins_handler(
                resources=resources,
                plugin_manager=plugin_manager,
            ),
            methods=("GET",),
        ),
        Route(
            "/api/plugins/install",
            make_install_plugin_handler(
                resources=resources,
                daemon=daemon,
                plugin_manager=plugin_manager,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("POST",),
        ),
        Route(
            "/api/plugins/{name}",
            make_uninstall_plugin_handler(
                resources=resources,
                daemon=daemon,
                plugin_manager=plugin_manager,
                _request_daemon_fn=_request_daemon,
            ),
            methods=("DELETE",),
        ),
    ]

    if trace_db_path:
        routes.append(
            Mount("/monitor/trace", app=build_trace_app(db_path=trace_db_path))
        )

    # Serve frontend static assets from /assets/
    web_dist = config.web_dist_dir or "web/dist"
    web_path = Path(web_dist).resolve()
    if web_path.is_dir():
        assets_path = web_path / "assets"
        if assets_path.is_dir():
            routes.append(
                Mount(
                    "/assets",
                    app=StaticFiles(directory=str(assets_path)),
                    name="assets",
                )
            )

        # Serve index.html explicitly at the root
        index_path = web_path / "index.html"
        serve_spa = make_serve_spa_handler(index_path=index_path)
        routes.append(Route("/{path:path}", serve_spa, methods=("GET",)))
        routes.append(Route("/", serve_spa, methods=("GET",)))

    return Starlette(routes=tuple(routes))


async def build_admin(
    config: BootstrapConfig,
    *,
    components: AdminInfrastructure | None = None,
) -> YuubotAdmin:
    config.validate()
    components = components or AdminInfrastructure()
    layout = DataLayout.from_path(config.paths.data_dir)
    layout.ensure()
    resources = await open_resources(config, migrate=False)
    daemon_url = f"http://{config.server.daemon_host}:{config.server.daemon_port}"
    plugin_manager = ExternalPluginManager(
        plugins_dir=layout.plugins_dir,
        data_root=layout.data_dir,
        daemon_host=config.server.daemon_host,
        daemon_port=config.server.daemon_port,
    )
    components.integration_factories.register_loader(
        ExternalPluginFactoryLoader(layout.plugins_dir)
    )

    trace_db_path = str(layout.traces_db_path)

    return YuubotAdmin(
        config=config.admin,
        resources=resources,
        daemon=DaemonClient(
            base_url=daemon_url,
            daemon_secret=config.server.daemon_secret,
        ),
        asgi_server=components.asgi_server,
        integration_factories=components.integration_factories,
        plugin_manager=plugin_manager,
        trace_db_path=trace_db_path,
    )
