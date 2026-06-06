"""Daemon service runtime.

Route handlers live in :mod:`yuubot.runtime.daemon.handlers`.
Authentication middleware lives in :mod:`yuubot.runtime.daemon.middleware`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount, Route

from yuubot.bootstrap.config import BootstrapConfig, ServerConfig
from yuubot.bootstrap.layout import DataLayout
from yuubot.core.actors import (
    ActorFactoryRegistry,
    ActorManager,
    ActorPythonSessionFactory,
    ActorWorkspaceResolver,
    default_actor_factories,
)
from yuubot.core.conversations import ConversationManager, ConversationStore
from yuubot.core.events import Event
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import (
    IntegrationCore,
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.core.routing import RouteBindings, load_route_bindings
from yuubot.resources.events import ResourceChanged
from yuubot.resources.registry import (
    EventDrivenRefreshDispatcher,
    LifecycleHandler,
    ResourceTypeRegistry,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.service import ResourceService
from yuubot.runtime.daemon.commands import (
    build_commands_app,
    build_default_resource_type_registry,
    in_command_context,
)
from yuubot.runtime.daemon.handlers import (
    _configuration_error_response,  # noqa: F401  # re-exported for compat
    _sse_event,  # noqa: F401  # re-exported for tests
    make_conversation_events_handler,
    make_conversation_messages_handler,
    make_create_conversation_handler,
    make_ensure_conversation_agent_handler,
    make_health_handler,
    make_list_conversations_handler,
    make_plugin_ingest_handler,
    make_refresh_handler,
    make_send_conversation_message_handler,
    make_status_handler,
)
from yuubot.runtime.daemon.middleware import DaemonSecretMiddleware
from yuubot.runtime.http_utils import error_response
from yuubot.runtime.plugin_manager import ExternalPluginManager
from yuubot.runtime.process import (
    ASGIServer,
    ServiceHost,
    TraceService,
    UvicornServer,
    open_resources,
)

logger = logging.getLogger(__name__)


# -- Backward-compatible aliases (used by tests) --

_error_response = error_response


# -- Infrastructure dataclasses --


@dataclass
class DaemonInfrastructure:
    integration_factories: IntegrationFactoryRegistry = field(
        default_factory=default_integration_factories
    )
    actor_factories: ActorFactoryRegistry | None = None
    trace_context: YuubotTraceContextProvider = field(
        default_factory=YuubotTraceContextProvider
    )
    asgi_server: ASGIServer = field(default_factory=UvicornServer)

    def trace_service(self, config: BootstrapConfig) -> TraceService:
        layout = DataLayout.from_path(config.paths.data_dir)
        return TraceService(config=config.trace, db_path=str(layout.traces_db_path))

    def actor_factory_registry(
        self,
        config: BootstrapConfig,
        python_sessions: ActorPythonSessionFactory,
        repository: ResourceRepository,
        integrations: IntegrationCore | None = None,
    ) -> ActorFactoryRegistry:
        return self.actor_factories or default_actor_factories(
            config.yuuagents,
            python_sessions,
            repository,
            trace_context=self.trace_context,
            integrations=integrations,
        )


@dataclass
class IntegrationLifecycleService:
    integrations: IntegrationCore
    name: str = "integrations"

    async def start(self) -> None:
        await self.integrations.reconcile()

    async def stop(self) -> None:
        await self.integrations.disable_all()


@dataclass
class RouteBindingService:
    repository: ResourceRepository
    gateway: Gateway
    name: str = "routes"

    async def start(self) -> None:
        await self.reload()

    async def stop(self) -> None:
        return

    async def reload(self) -> None:
        self.gateway.update_bindings(await load_route_bindings(self.repository))


@dataclass
class ActorLifecycleService:
    actors: ActorManager
    name: str = "actors"

    async def start(self) -> None:
        await self.actors.reconcile()

    async def stop(self) -> None:
        await self.actors.stop_all()


def build_refresh_dispatcher(
    *,
    routes: RouteBindingService,
    actors: ActorManager,
    integrations: IntegrationCore,
) -> EventDrivenRefreshDispatcher:
    """Build an event-driven refresh dispatcher with per-table handlers.

    Replaces the hardcoded if/elif chain — new resource types register
    their own handlers without modifying this function.
    """
    dispatcher = EventDrivenRefreshDispatcher()

    async def on_ingress_rules_change(event: ResourceChanged) -> list[str]:
        await routes.reload()
        await actors.reconcile()
        return ["routes.reloaded", "actors.reconciled"]

    async def on_actor_change(event: ResourceChanged) -> list[str]:
        actions: list[str] = []
        await routes.reload()
        actions.append("routes.reloaded")
        await integrations.handle_resource_changed(event)
        actions.append("integrations.actor_cache_invalidated")
        await actors.reconcile()
        actions.append("actors.reconciled")
        return actions

    async def on_character_or_llm_change(event: ResourceChanged) -> list[str]:
        await actors.forward_resource_change(event)
        return ["actors.notified"]

    async def on_integration_change(event: ResourceChanged) -> list[str]:
        await integrations.reconcile(event)
        return ["integrations.reconciled", "capabilities.reloaded"]

    dispatcher.on("actor_ingress_rules", on_ingress_rules_change)
    dispatcher.on("actors", on_actor_change)
    dispatcher.on("characters", on_character_or_llm_change)
    dispatcher.on("llm_backends", on_character_or_llm_change)
    dispatcher.on("integrations", on_integration_change)

    return dispatcher


@dataclass
class YuubotDaemon:
    """Running daemon service."""

    config: ServerConfig
    resources: Resources
    actors: ActorManager
    integrations: IntegrationCore
    plugin_manager: ExternalPluginManager
    gateway: Gateway
    services: ServiceHost
    asgi_server: ASGIServer
    refresh: EventDrivenRefreshDispatcher
    trace_service: TraceService
    type_registry: ResourceTypeRegistry

    async def start(self) -> None:
        await self.services.start()

    async def stop(self) -> None:
        try:
            if self.services.started:
                await self.services.stop()
        finally:
            await self.resources.close()

    def asgi_app(self) -> Starlette:
        return build_daemon_asgi_app(
            config=self.config,
            resources=self.resources,
            services=self.services,
            actors=self.actors,
            integrations=self.integrations,
            plugin_manager=self.plugin_manager,
            gateway=self.gateway,
            refresh=self.refresh,
            trace_service=self.trace_service,
            type_registry=self.type_registry,
        )

    async def serve(self) -> None:
        try:
            await self.asgi_server.serve(
                self.asgi_app(),
                host=self.config.daemon_host,
                port=self.config.daemon_port,
            )
        finally:
            await self.resources.close()


def _integration_lifecycle_handler(
    integrations: IntegrationCore,
) -> LifecycleHandler:
    """Create a lifecycle handler for integration enable/disable."""

    async def handle(row_id: str, label: str) -> list[str]:
        await integrations.reconcile(
            ResourceChanged(
                table="integrations",
                action="updated",
                row_ids=(row_id,),
                changed_fields=("enabled",),
            )
        )
        return [f"integration.{label}d"]

    return handle


def _actor_lifecycle_handler(
    actors: ActorManager,
) -> LifecycleHandler:
    """Create a lifecycle handler for actor enable/disable."""

    async def handle(row_id: str, label: str) -> list[str]:
        await actors.reconcile()
        return [f"actor.{label}d"]

    return handle


def build_daemon_asgi_app(
    *,
    config: ServerConfig,
    resources: Resources,
    services: ServiceHost,
    actors: ActorManager,
    integrations: IntegrationCore,
    plugin_manager: ExternalPluginManager | None = None,
    gateway: Gateway,
    refresh: EventDrivenRefreshDispatcher,
    trace_service: TraceService,
    type_registry: ResourceTypeRegistry,
) -> Starlette:
    """Construct the daemon ASGI application.

    All route handlers are created by factory functions in
    :mod:`yuubot.runtime.daemon.handlers`.  Authentication is
    enforced by :class:`DaemonSecretMiddleware`.
    """
    if plugin_manager is None:
        layout = DataLayout.from_path("~/.yuubot")
        plugin_manager = ExternalPluginManager(
            plugins_dir=layout.plugins_dir,
            data_root=layout.data_dir,
        )

    conversation_manager = ConversationManager(
        store=ConversationStore(resources.store),
        actors=actors,
    )

    resource_service = ResourceService(
        repository=resources.repository,
        refresh=refresh,
        integrations=integrations,
        actors=actors,
        type_registry=type_registry,
    )

    integration_routes = integrations.factories.collect_routes(integrations)

    # --- lifespan ---

    @asynccontextmanager
    async def lifespan(_: Starlette):
        await services.start()
        try:
            yield
        finally:
            await services.stop()

    # --- route table ---

    routes: list[Route | Mount] = [
        Route(
            "/healthz",
            make_health_handler(config, resources, plugin_manager),
            methods=("GET",),
        ),
        *integration_routes,
        Route(
            "/ingest",
            make_plugin_ingest_handler(plugin_manager, integrations),
            methods=("POST",),
        ),
        Route(
            "/api/status",
            make_status_handler(
                services, actors, integrations, plugin_manager,
                gateway, trace_service,
            ),
            methods=("GET",),
        ),
        Route(
            "/api/admin/refresh",
            make_refresh_handler(refresh),
            methods=("POST",),
        ),
        Route(
            "/api/admin/conversations",
            make_list_conversations_handler(conversation_manager),
            methods=("GET",),
        ),
        Route(
            "/api/admin/conversations",
            make_create_conversation_handler(conversation_manager),
            methods=("POST",),
        ),
        Route(
            "/api/admin/conversations/{conversation_id}/agents",
            make_ensure_conversation_agent_handler(conversation_manager),
            methods=("POST",),
        ),
        Route(
            "/api/admin/conversations/{conversation_id}/events",
            make_conversation_events_handler(conversation_manager),
            methods=("GET",),
        ),
        Route(
            "/api/admin/conversations/{conversation_id}/messages",
            make_conversation_messages_handler(conversation_manager),
            methods=("GET",),
        ),
        Route(
            "/api/admin/conversations/{conversation_id}/messages",
            make_send_conversation_message_handler(conversation_manager),
            methods=("POST",),
        ),
        Mount(
            "/api/resources",
            app=build_commands_app(
                resource_service,
                type_registry,
                resources.repository,
                config,
            ),
        ),
    ]

    return Starlette(
        routes=routes,
        middleware=[
            Middleware(
                DaemonSecretMiddleware, secret=config.daemon_secret
            ),
        ],
        lifespan=lifespan,
    )


async def build_daemon(
    config: BootstrapConfig,
    *,
    components: DaemonInfrastructure | None = None,
) -> YuubotDaemon:
    config.validate()
    components = components or DaemonInfrastructure()
    layout = DataLayout.from_path(config.paths.data_dir)
    layout.ensure()
    resources = await open_resources(config)

    repository = resources.repository
    gateway = Gateway(routes=RouteBindings(rules=[]))
    plugin_manager = ExternalPluginManager(
        plugins_dir=layout.plugins_dir,
        data_root=layout.data_dir,
        daemon_host=config.server.daemon_host,
        daemon_port=config.server.daemon_port,
    )
    components.integration_factories.register_loader(plugin_manager.loader())
    integrations = IntegrationCore(
        repository=repository,
        factories=components.integration_factories,
        gateway=gateway,
        integrations_root=layout.integrations_root,
    )
    actor_python_sessions = ActorPythonSessionFactory.in_directory(
        integrations=integrations,
        root=layout.runtime_facades_dir,
        mailbox_for_actor=gateway.find_mailbox,
    )
    actors = ActorManager(
        repository=repository,
        factories=components.actor_factory_registry(
            config, actor_python_sessions, repository, integrations
        ),
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(layout.workspace_root),
    )

    async def schedule_for_actor(
        actor_id: str,
        agent_name: str,
        tool_name: str,
        payload: dict[str, object],
    ) -> object:
        from yuubot.core.actors.impls.simple_loop import SimpleLoopActor

        actor = actors.running_actor(actor_id)
        if actor is None:
            raise LookupError(f"actor is not running: {actor_id!r}")
        if not isinstance(actor, SimpleLoopActor):
            raise RuntimeError(
                f"actor does not support schedule tools: {actor_id!r}"
            )
        return await actor.run_schedule_tool(
            agent_name=agent_name,
            tool_name=tool_name,
            payload=payload,
        )

    actor_python_sessions.bridge.schedule_for_actor = schedule_for_actor
    routes = RouteBindingService(repository=repository, gateway=gateway)
    refresh = build_refresh_dispatcher(
        routes=routes,
        actors=actors,
        integrations=integrations,
    )

    type_registry = build_default_resource_type_registry(
        integration_lifecycle_handler=_integration_lifecycle_handler(
            integrations
        ),
        actor_lifecycle_handler=_actor_lifecycle_handler(actors),
    )

    async def on_resources_changed(event: Event) -> None:
        if not isinstance(event, ResourceChanged):
            return
        if in_command_context.get(False):
            return
        await refresh.refresh(event)

    resources.event_bus.subscribe([ResourceChanged], on_resources_changed)

    trace_svc = components.trace_service(config)
    return YuubotDaemon(
        config=config.server,
        resources=resources,
        actors=actors,
        integrations=integrations,
        plugin_manager=plugin_manager,
        gateway=gateway,
        services=ServiceHost.from_iterable(
            (
                resources.event_bus,
                trace_svc,
                plugin_manager,
                IntegrationLifecycleService(integrations),
                actor_python_sessions,
                routes,
                ActorLifecycleService(actors),
            )
        ),
        asgi_server=components.asgi_server,
        refresh=refresh,
        trace_service=trace_svc,
        type_registry=type_registry,
    )
