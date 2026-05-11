"""Daemon service runtime."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import msgspec
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from yuubot.bootstrap.config import BootstrapConfig, ServerConfig
from yuubot.core.actors import (
    ActorFactoryRegistry,
    ActorManager,
    ActorPythonSessionFactory,
    ActorWorkspaceResolver,
    default_actor_factories,
)
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import (
    IntegrationCore,
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.integrations.echo import EchoIngressPayload, EchoIntegration
from yuubot.core.routing import RouteBindings, load_route_bindings
from yuubot.events import Event
from yuubot.process import (
    ASGIServer,
    ServiceHost,
    TraceService,
    UvicornServer,
    open_resources,
)
from yuubot.resources.events import ResourceChanged
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM
from yuubot.runtime.commands import build_commands_app, in_command_context

logger = logging.getLogger(__name__)


@dataclass
class DaemonInfrastructure:
    integration_factories: IntegrationFactoryRegistry = field(
        default_factory=default_integration_factories
    )
    actor_factories: ActorFactoryRegistry | None = None
    asgi_server: ASGIServer = field(default_factory=UvicornServer)

    def trace_service(self, config: BootstrapConfig) -> TraceService:
        return TraceService(config=config.trace)

    def actor_factory_registry(
        self,
        config: BootstrapConfig,
        python_sessions: ActorPythonSessionFactory,
        repository: ResourceRepository,
    ) -> ActorFactoryRegistry:
        return self.actor_factories or default_actor_factories(
            config.yuuagents,
            python_sessions,
            repository,
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


@dataclass
class DaemonRefreshDispatcher:
    routes: RouteBindingService
    actors: ActorManager
    integrations: IntegrationCore

    async def refresh(self, event: ResourceChanged) -> tuple[str, ...]:
        actions: list[str] = []
        if event.is_table("actor_ingress_rules"):
            await self.routes.reload()
            actions.append("routes.reloaded")
            await self.actors.reconcile()
            actions.append("actors.reconciled")
            return tuple(actions)
        if event.is_table("actors"):
            await self.routes.reload()
            actions.append("routes.reloaded")
            await self.integrations.handle_resource_changed(event)
            actions.append("integrations.actor_cache_invalidated")
            await self.actors.reconcile()
            actions.append("actors.reconciled")
            return tuple(actions)
        if event.is_table("characters", "llm_backends"):
            await self.actors.forward_resource_change(event)
            actions.append("actors.notified")
            return tuple(actions)
        if event.is_table("integrations"):
            await self.integrations.reconcile(event)
            return ("integrations.reconciled", "capabilities.reloaded")
        return ()


@dataclass
class YuubotDaemon:
    """Running daemon service."""

    config: ServerConfig
    resources: Resources
    actors: ActorManager
    integrations: IntegrationCore
    gateway: Gateway
    services: ServiceHost
    asgi_server: ASGIServer
    refresh: DaemonRefreshDispatcher
    trace_enabled: bool

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
            gateway=self.gateway,
            refresh=self.refresh,
            trace_enabled=self.trace_enabled,
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


def build_daemon_asgi_app(
    *,
    config: ServerConfig,
    resources: Resources,
    services: ServiceHost,
    actors: ActorManager,
    integrations: IntegrationCore,
    gateway: Gateway,
    refresh: DaemonRefreshDispatcher,
    trace_enabled: bool,
) -> Starlette:
    @asynccontextmanager
    async def lifespan(_: Starlette):
        await services.start()
        try:
            yield
        finally:
            await services.stop()

    async def health(_: Request) -> JSONResponse:
        ingress_rules = await resources.repository.list(ActorIngressRuleORM)
        integrations = await resources.repository.list(IntegrationORM)
        return JSONResponse(
            {
                "status": "ok",
                "daemon": f"{config.daemon_host}:{config.daemon_port}",
                "ingress_rules": len(ingress_rules),
                "integrations": len(integrations),
            }
        )

    async def status(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        return JSONResponse(
            {
                "status": "running" if services.started else "stopped",
                "running_integration_ids": list(
                    integrations.running_integration_ids()
                ),
                "running_actor_ids": list(actors.running_actor_ids()),
                "actor_workspaces": actors.running_actor_workspace_paths(),
                "route_binding_count": gateway.routes.binding_count(),
                "trace": {
                    "enabled": trace_enabled,
                    "status": "enabled" if trace_enabled else "disabled",
                },
            }
        )

    async def refresh_resources(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        event_or_response = await _resource_changed_from_request(request)
        if isinstance(event_or_response, JSONResponse):
            return event_or_response

        event = event_or_response
        try:
            actions = await refresh.refresh(event)
        except Exception as exc:
            logger.exception("daemon refresh failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse(
            {
                "status": "ok",
                "event": event.to_dict(),
                "actions": list(actions),
            }
        )

    async def echo_ingress(request: Request) -> JSONResponse:
        payload_or_response = await _echo_payload_from_request(request)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response

        payload = payload_or_response
        try:
            instance = _resolve_echo_instance(integrations, payload.integration_id)
            message = await instance.emit_payload(payload)
        except LookupError as exc:
            return _error_response(str(exc), status_code=404)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        except Exception as exc:
            logger.exception("echo integration ingress failed")
            return _error_response(str(exc), status_code=500)

        return JSONResponse(
            {
                "status": "ok",
                "integration_id": instance.ingress.integration_id,
                "message_id": message.message_id,
                "source": msgspec.to_builtins(message.source),
            },
            status_code=202,
        )

    return Starlette(
        routes=(
            Route("/healthz", health, methods=("GET",)),
            Route("/integration/echo", echo_ingress, methods=("POST",)),
            Route("/api/status", status, methods=("GET",)),
            Route("/api/admin/refresh", refresh_resources, methods=("POST",)),
            Mount(
                "/api/resources",
                app=build_commands_app(
                    resources.repository, refresh, integrations, actors, config,
                ),
            ),
        ),
        lifespan=lifespan,
    )


async def build_daemon(
    config: BootstrapConfig,
    *,
    components: DaemonInfrastructure | None = None,
) -> YuubotDaemon:
    config.validate()
    components = components or DaemonInfrastructure()
    resources = await open_resources(config)

    repository = resources.repository
    gateway = Gateway(routes=RouteBindings(rules=()))
    integrations = IntegrationCore(
        repository=repository,
        factories=components.integration_factories,
        gateway=gateway,
    )
    actor_python_sessions = ActorPythonSessionFactory.in_directory(
        integrations=integrations,
        root=Path(config.paths.data_dir) / "runtime" / "facades",
    )
    actors = ActorManager(
        repository=repository,
        factories=components.actor_factory_registry(config, actor_python_sessions, repository),
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(Path(config.paths.workspace_dir)),
    )
    routes = RouteBindingService(repository=repository, gateway=gateway)
    refresh = DaemonRefreshDispatcher(
        routes=routes,
        actors=actors,
        integrations=integrations,
    )

    async def on_resources_changed(event: Event) -> None:
        if not isinstance(event, ResourceChanged):
            return
        if in_command_context.get(False):
            return
        await refresh.refresh(event)

    resources.event_bus.subscribe([ResourceChanged], on_resources_changed)

    return YuubotDaemon(
        config=config.server,
        resources=resources,
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        services=ServiceHost.from_iterable(
            (
                resources.event_bus,
                components.trace_service(config),
                IntegrationLifecycleService(integrations),
                actor_python_sessions,
                routes,
                ActorLifecycleService(actors),
            )
        ),
        asgi_server=components.asgi_server,
        refresh=refresh,
        trace_enabled=config.trace.enabled,
    )


def _daemon_secret_error(config: ServerConfig, request: Request) -> str | None:
    if not config.daemon_secret:
        return "server.daemon_secret is not configured"
    if request.headers.get("x-daemon-secret") != config.daemon_secret:
        return "X-Daemon-Secret is missing or invalid"
    return None


async def _resource_changed_from_request(
    request: Request,
) -> ResourceChanged | JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return _error_response("request body must be a JSON object", status_code=400)
    try:
        return ResourceChanged.from_dict(cast(dict[str, object], payload))
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)


def _error_response(reason: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "reason": reason},
        status_code=status_code,
    )


async def _echo_payload_from_request(
    request: Request,
) -> EchoIngressPayload | JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return _error_response("request body must be a JSON object", status_code=400)
    try:
        return msgspec.convert(
            cast(dict[str, Any], payload),
            type=EchoIngressPayload,
            strict=False,
        )
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        return _error_response(str(exc), status_code=400)


def _resolve_echo_instance(
    integrations: IntegrationCore,
    integration_id: str,
) -> EchoIntegration:
    if integration_id:
        instance = integrations.running_instance(integration_id)
        if not isinstance(instance, EchoIntegration):
            raise LookupError(f"integration {integration_id!r} is not an echo integration")
        return instance

    matches: list[EchoIntegration] = []
    for running_id in integrations.running_integration_ids():
        instance = integrations.running_instance(running_id)
        if isinstance(instance, EchoIntegration):
            matches.append(instance)
    if not matches:
        raise LookupError("no running echo integration")
    if len(matches) > 1:
        raise ValueError("integration_id is required when multiple echo integrations run")
    return matches[0]
