"""Daemon service runtime."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

import msgspec
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
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
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import (
    IntegrationCore,
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.chat_store import ChatStore
from yuubot.core.conversations import ConversationManager, ConversationStore
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.core.routing import RouteBindings, load_route_bindings
from yuubot.core.events import Event
from yuubot.core.system_caps import SystemCapHandler
from yuubot.core.validation import ConfigurationError
from yuubot.runtime.http_utils import error_response
from yuubot.runtime.process import (
    ASGIServer,
    ServiceHost,
    TraceService,
    UvicornServer,
    open_resources,
)
from yuubot.resources.events import ResourceChanged
from yuubot.resources.registry import EventDrivenRefreshDispatcher, LifecycleHandler, ResourceTypeRegistry
from yuubot.resources.service import ResourceService
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM
from yuubot.runtime.daemon.commands import (
    build_commands_app,
    build_default_resource_type_registry,
    in_command_context,
)
from yuubot.runtime.plugin_manager import (
    ExternalPluginInboundMessage,
    ExternalPluginIntegration,
    ExternalPluginManager,
)

logger = logging.getLogger(__name__)


# -- Request Structs: typed boundary for daemon HTTP payloads --


class ConversationRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for conversation creation requests."""

    actor_id: str = ""
    conversation_id: str = ""


class ConversationMessageRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for conversation message requests."""

    text: str = ""
    content: list[dict[str, object]] = msgspec.field(default_factory=list)
    message_id: str = ""


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
    chat_store: ChatStore | None = None

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
            chat_store=self.chat_store,
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
    chat_store: ChatStore | None = None,
) -> Starlette:
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
                "external_plugins": len(plugin_manager.statuses()),
            }
        )

    async def status(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        body: dict[str, object] = {
            "status": "running" if services.started else "stopped",
            "running_integration_ids": integrations.running_integration_ids(),
            "running_actor_ids": actors.running_actor_ids(),
            "actor_workspaces": actors.running_actor_workspace_paths(),
            "route_binding_count": gateway.routes.binding_count(),
            "trace": {
                "enabled": trace_service.config.enabled,
                "status": trace_service.status,
            },
        }
        actor_failures = actors.startup_failures()
        if actor_failures:
            body["actor_startup_failures"] = [
                {
                    "actor_id": failure.actor_id,
                    "detail": failure.detail,
                }
                for failure in actor_failures
            ]
        plugin_statuses = plugin_manager.statuses()
        if plugin_statuses:
            body["external_plugins"] = [
                {
                    "name": status.name,
                    "integration_id": status.integration_id,
                    "port": status.port,
                    "healthy": status.healthy,
                    "pid": status.pid,
                }
                for status in plugin_statuses
            ]
        return JSONResponse(body)

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
        except ConfigurationError as exc:
            return _configuration_error_response(exc)
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

    async def plugin_ingest(request: Request) -> JSONResponse:
        payload_or_response = await _plugin_ingest_from_request(request, plugin_manager)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response
        payload = payload_or_response

        try:
            instance = _resolve_external_plugin_instance(
                integrations,
                payload.integration_id,
            )
            message = await instance.emit_payload(payload)
        except PermissionError as exc:
            return _error_response(str(exc), status_code=403)
        except LookupError as exc:
            return _error_response(str(exc), status_code=404)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        except Exception as exc:
            logger.exception("external plugin ingest failed")
            return _error_response(str(exc), status_code=500)

        return JSONResponse(
            {
                "status": "ok",
                "integration_id": payload.integration_id,
                "message_id": message.message_id,
                "source": msgspec.to_builtins(message.source),
            },
            status_code=202,
        )

    async def create_conversation(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        payload_or_response = await _conversation_payload_from_request(request)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response
        payload = payload_or_response
        conversation_id = payload.get("conversation_id") or uuid.uuid4().hex
        try:
            conversation = await conversation_manager.create_conversation(
                conversation_id=conversation_id,
                actor_id=payload["actor_id"],
            )
        except Exception as exc:
            logger.exception("create conversation failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "conversation_id": conversation.conversation_id,
                    "actor_id": conversation.actor_id,
                    "created_at": _iso_or_none(conversation.created_at),
                    "updated_at": _iso_or_none(conversation.updated_at),
                },
            },
            status_code=201,
        )

    async def list_conversations(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        try:
            conversations = await conversation_manager.store.list_conversations(
                actor_id=request.query_params.get("actor_id"),
            )
        except Exception as exc:
            logger.exception("list conversations failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse(
            {
                "status": "ok",
                "data": [
                    {
                        "conversation_id": item.conversation_id,
                        "actor_id": item.actor_id,
                        "created_at": _iso_or_none(item.created_at),
                        "updated_at": _iso_or_none(item.updated_at),
                    }
                    for item in conversations
                ],
            }
        )

    async def ensure_conversation_agent(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        conversation_id = request.path_params["conversation_id"]
        try:
            data = await conversation_manager.ensure_agent(conversation_id)
        except LookupError as exc:
            return _error_response(str(exc), status_code=404)
        except ConfigurationError as exc:
            return _configuration_error_response(exc)
        except Exception as exc:
            logger.exception("ensure conversation agent failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse({"status": "ok", "data": data})

    async def conversation_messages(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        conversation_id = request.path_params["conversation_id"]
        try:
            messages = await conversation_manager.store.list_messages(conversation_id)
        except Exception as exc:
            logger.exception("list conversation messages failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse(
            {
                "status": "ok",
                "data": [
                    {
                        "id": message.id,
                        "message_id": message.message_id,
                        "conversation_id": message.conversation_id,
                        "role": message.role,
                        "raw_content": message.raw_content,
                        "metadata": message.metadata,
                        "timestamp": message.timestamp,
                    }
                    for message in messages
                ],
            }
        )

    async def send_conversation_message(request: Request) -> JSONResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return _error_response(error, status_code=403)
        payload_or_response = await _conversation_message_payload_from_request(request)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response
        payload = payload_or_response
        conversation_id = request.path_params["conversation_id"]
        try:
            record = await conversation_manager.send_message(
                conversation_id=conversation_id,
                content=cast(list[dict[str, object]], payload["content"]),
                message_id=cast(str | None, payload.get("message_id")),
            )
        except LookupError as exc:
            return _error_response(str(exc), status_code=404)
        except ConfigurationError as exc:
            return _configuration_error_response(exc)
        except Exception as exc:
            logger.exception("send conversation message failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse(
            {
                "status": "accepted",
                "data": {
                    "conversation_id": record.conversation_id,
                    "message_id": record.message_id,
                },
            },
            status_code=202,
        )

    async def conversation_events(request: Request) -> StreamingResponse:
        error = _daemon_secret_error(config, request)
        if error is not None:
            return StreamingResponse(
                iter((_sse_event("error", {"status": "error", "error": error}),)),
                status_code=403,
                media_type="text/event-stream",
            )
        conversation_id = request.path_params["conversation_id"]

        async def stream():
            try:
                async for event in conversation_manager.subscribe_events(conversation_id):
                    if await request.is_disconnected():
                        break
                    yield _sse_event(event.event_type, event.as_dict())
            except Exception:
                logger.exception("conversation SSE stream failed for %r", conversation_id)
                yield _sse_event("error", {
                    "status": "error",
                    "error": "conversation stream terminated unexpectedly",
                })

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def chat_dialogs(request: Request) -> JSONResponse:
        if chat_store is None:
            return _error_response("chat store not available", status_code=503)
        logger.info("chat: listing dialogs")
        try:
            dialogs = await chat_store.list_dialogs()
        except Exception as exc:
            logger.exception("list dialogs failed")
            return _error_response(str(exc), status_code=500)
        return JSONResponse({
            "status": "ok",
            "data": [
                {
                    "dialog_id": d.dialog_id,
                    "message_count": d.message_count,
                    "last_message_preview": d.last_message_preview,
                    "updated_at": d.updated_at,
                }
                for d in dialogs
            ],
        })

    async def chat_dialog_messages(request: Request) -> JSONResponse:
        if chat_store is None:
            return _error_response("chat store not available", status_code=503)
        dialog_id = request.path_params["dialog_id"]
        q = request.query_params.get("q")
        before = request.query_params.get("before")
        after = request.query_params.get("after")
        since_str = request.query_params.get("since")
        until_str = request.query_params.get("until")
        limit_str = request.query_params.get("limit", "50")
        role = request.query_params.get("role")

        logger.info(
            "chat: browsing messages dialog_id=%s limit=%s q=%s role=%s",
            dialog_id, limit_str, q, role,
        )

        try:
            limit = int(limit_str)
        except (ValueError, TypeError):
            return _error_response("limit must be an integer", status_code=400)

        try:
            since = int(since_str) if since_str is not None else None
            until = int(until_str) if until_str is not None else None

            if q:
                result = await chat_store.search_messages(dialog_id, q, limit=limit)
            else:
                result = await chat_store.browse_messages(
                    dialog_id,
                    before=before,
                    after=after,
                    since=since,
                    until=until,
                    limit=limit,
                    role=role,
                )
        except Exception as exc:
            logger.exception("browse messages failed")
            return _error_response(str(exc), status_code=500)

        return JSONResponse({
            "status": "ok",
            "data": {
                "messages": [
                    {
                        "id": m.id,
                        "dialog_id": m.dialog_id,
                        "message_id": m.message_id,
                        "role": m.role,
                        "raw_content": m.raw_content,
                        "text_content": m.text_content,
                        "actor_id": m.actor_id,
                        "sender_id": m.sender_id,
                        "sender_name": m.sender_name,
                        "timestamp": m.timestamp,
                    }
                    for m in result.messages
                ],
                "has_more": result.has_more,
            },
        })

    async def chat_message_by_id(request: Request) -> JSONResponse:
        if chat_store is None:
            return _error_response("chat store not available", status_code=503)
        message_id = request.path_params["message_id"]
        logger.info("chat: get message message_id=%s", message_id)
        try:
            message = await chat_store.get_message(message_id)
        except Exception as exc:
            logger.exception("get message failed")
            return _error_response(str(exc), status_code=500)
        if message is None:
            return _error_response(
                f"message {message_id!r} not found", status_code=404
            )
        return JSONResponse({
            "status": "ok",
            "data": {
                "id": message.id,
                "dialog_id": message.dialog_id,
                "message_id": message.message_id,
                "role": message.role,
                "raw_content": message.raw_content,
                "text_content": message.text_content,
                "actor_id": message.actor_id,
                "sender_id": message.sender_id,
                "sender_name": message.sender_name,
                "timestamp": message.timestamp,
            },
        })

    resource_service = ResourceService(
        repository=resources.repository,
        refresh=refresh,
        integrations=integrations,
        actors=actors,
        type_registry=type_registry,
    )

    integration_routes = integrations.factories.collect_routes(integrations)

    return Starlette(
        routes=(
            Route("/healthz", health, methods=("GET",)),
            *integration_routes,
            Route("/ingest", plugin_ingest, methods=("POST",)),
            Route("/api/status", status, methods=("GET",)),
            Route("/api/admin/refresh", refresh_resources, methods=("POST",)),
            Route(
                "/api/conversations",
                list_conversations,
                methods=("GET",),
            ),
            Route(
                "/api/conversations",
                create_conversation,
                methods=("POST",),
            ),
            Route(
                "/api/conversations/{conversation_id}/agents",
                ensure_conversation_agent,
                methods=("POST",),
            ),
            Route(
                "/api/conversations/{conversation_id}/events",
                conversation_events,
                methods=("GET",),
            ),
            Route(
                "/api/conversations/{conversation_id}/messages",
                conversation_messages,
                methods=("GET",),
            ),
            Route(
                "/api/conversations/{conversation_id}/messages",
                send_conversation_message,
                methods=("POST",),
            ),
            Route(
                "/api/chat/dialogs",
                chat_dialogs,
                methods=("GET",),
            ),
            Route(
                "/api/chat/dialogs/{dialog_id}/messages",
                chat_dialog_messages,
                methods=("GET",),
            ),
            Route(
                "/api/chat/messages/{message_id}",
                chat_message_by_id,
                methods=("GET",),
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
    layout = DataLayout.from_path(config.paths.data_dir)
    layout.ensure()
    resources = await open_resources(config)

    chat_store = ChatStore(resources.store)

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
            raise RuntimeError(f"actor does not support schedule tools: {actor_id!r}")
        return await actor.run_schedule_tool(
            agent_name=agent_name,
            tool_name=tool_name,
            payload=payload,
        )

    actor_python_sessions.bridge.schedule_for_actor = schedule_for_actor
    actor_python_sessions.bridge.system_caps = SystemCapHandler(chat_store=chat_store)

    routes = RouteBindingService(repository=repository, gateway=gateway)
    refresh = build_refresh_dispatcher(
        routes=routes,
        actors=actors,
        integrations=integrations,
    )

    type_registry = build_default_resource_type_registry(
        integration_lifecycle_handler=_integration_lifecycle_handler(integrations),
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
        chat_store=chat_store,
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
    except json.JSONDecodeError:
        return _error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return _error_response("request body must be a JSON object", status_code=400)
    try:
        return ResourceChanged.from_dict(cast(dict[str, object], payload))
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)


async def _conversation_payload_from_request(
    request: Request,
) -> dict[str, str] | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return _error_response("request body must be a JSON object", status_code=400)

    try:
        req = msgspec.convert(payload, type=ConversationRequest, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError):
        return _error_response("invalid request body", status_code=400)

    if not req.actor_id.strip():
        return _error_response("actor_id must be a non-empty string", status_code=400)

    return {
        "actor_id": req.actor_id.strip(),
        "conversation_id": req.conversation_id.strip(),
    }


async def _conversation_message_payload_from_request(
    request: Request,
) -> dict[str, object] | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return _error_response("request body must be a JSON object", status_code=400)

    try:
        req = msgspec.convert(payload, type=ConversationMessageRequest, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError):
        return _error_response("invalid request body", status_code=400)

    content = _content_items_from_request(req)
    if isinstance(content, JSONResponse):
        return content

    message_id = req.message_id.strip() or None

    return {
        "content": content,
        "message_id": message_id,
    }


def _content_items_from_request(
    req: ConversationMessageRequest,
) -> list[dict[str, object]] | JSONResponse:
    if req.text.strip():
        return [{"type": "text", "text": req.text.strip()}]

    if not req.content:
        return _error_response(
            "text or content must be provided",
            status_code=400,
        )

    result: list[dict[str, object]] = []
    for item in req.content:
        result.append({str(key): value for key, value in item.items()})
    return result


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sse_event(event_type: str, data: object) -> str:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(data, ensure_ascii=True)}\n\n"
    )


_error_response = error_response


def _configuration_error_response(exc: ConfigurationError) -> JSONResponse:
    return _error_response(
        str(exc),
        status_code=400,
        code="configuration_error",
        hint=(
            "Configure pricing.entries for the selected LLM backend model "
            "or disable the USD budget before chatting."
        ),
    )


async def _plugin_ingest_from_request(
    request: Request,
    plugin_manager: ExternalPluginManager,
) -> ExternalPluginInboundMessage | JSONResponse:
    token = _bearer_token(request)
    if token is None:
        return _error_response("Authorization bearer token is missing", status_code=403)
    try:
        expected_integration_id = plugin_manager.integration_id_for_token(token)
    except PermissionError as exc:
        return _error_response(str(exc), status_code=403)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return _error_response("request body must be a JSON object", status_code=400)
    try:
        message = msgspec.convert(
            payload,
            type=ExternalPluginInboundMessage,
            strict=False,
        )
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        return _error_response(str(exc), status_code=400)
    if message.integration_id != expected_integration_id:
        return _error_response(
            "integration_id does not match plugin token", status_code=403
        )
    return message


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _resolve_external_plugin_instance(
    integrations: IntegrationCore,
    integration_id: str,
) -> ExternalPluginIntegration:
    instance = integrations.running_instance(integration_id)
    if not isinstance(instance, ExternalPluginIntegration):
        raise LookupError(f"integration {integration_id!r} is not an external plugin")
    return instance
