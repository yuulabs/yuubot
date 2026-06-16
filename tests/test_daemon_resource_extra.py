"""Daemon CRUD tests for additional resource types: characters, capability sets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from starlette.types import ASGIApp

from yuubot.bootstrap.config import ServerConfig, TraceConfig, YuuAgentsConfig
from yuubot.core.actors import Actor, ActorFactoryRegistry, ActorManager
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.assembly import llm_session_factory_for_binding
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import FacadeWorkspace, IntegrationInvokeBridge
from yuubot.core.gateway import Gateway, Mailbox
from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
from yuubot.core.routing import RouteBindings
from yuubot.process import ServiceHost, TraceService
from yuubot.resources.events import ResourceChanged
from yuubot.resources.root import Resources
from yuubot.runtime.daemon.commands import build_default_resource_type_registry
from yuubot.runtime.daemon import (
    IntegrationLifecycleService,
    RouteBindingService,
    build_daemon_asgi_app,
    build_refresh_dispatcher,
)

SECRET = "test-secret"
HEADERS = {"X-Daemon-Secret": SECRET}


@dataclass
class NullActor:
    @property
    def actor_id(self) -> str:
        return "null"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        _ = event

    async def handle_message(self, message) -> None:
        _ = message


@dataclass
class NullActorFactory:
    actor_type: str = "null"

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        _ = binding, mailbox
        return NullActor()


@dataclass
class RuntimeHarness:
    services: ServiceHost
    app: ASGIApp


def _build_runtime(
    resources: Resources,
    workspace_root: Path,
) -> RuntimeHarness:
    gateway = Gateway(routes=RouteBindings(rules=()))
    actor_factories = ActorFactoryRegistry()
    actor_factories.register(NullActorFactory())
    actors = ActorManager(
        repository=resources.repository,
        factories=actor_factories,
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(workspace_root / "workspaces"),
    )
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=IntegrationFactoryRegistry(),
        gateway=gateway,
        integrations_root=workspace_root / "data" / "integrations",
    )
    routes = RouteBindingService(repository=resources.repository, gateway=gateway)
    services = ServiceHost.from_iterable(
        (
            IntegrationLifecycleService(integrations),
            routes,
        )
    )
    refresh = build_refresh_dispatcher(
        routes=routes, actors=actors, integrations=integrations
    )
    type_registry = build_default_resource_type_registry()
    trace_service = TraceService(
        config=TraceConfig(enabled=False), db_path=":memory:"
    )
    python_sessions = ActorPythonSessionFactory(
        integrations=integrations,
        workspace=FacadeWorkspace(workspace_root / "facades"),
        bridge=IntegrationInvokeBridge(integrations),
    )
    app = build_daemon_asgi_app(
        config=ServerConfig(daemon_secret=SECRET),
        resources=resources,
        services=services,
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        refresh=refresh,
        trace_service=trace_service,
        type_registry=type_registry,
        yuuagents_config=YuuAgentsConfig(),
        python_sessions=python_sessions,
        llm_session_factory_factory=llm_session_factory_for_binding,
    )
    return RuntimeHarness(services=services, app=app)


def _client(runtime: RuntimeHarness) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.app),
        base_url="http://testserver",
    )


async def test_create_character(resources: Resources, tmp_path: Path) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/characters",
                headers=HEADERS,
                json={
                    "id": "char-e2e",
                    "name": "char-e2e",
                    "description": "E2E test character",
                    "system_prompt": "You are an E2E test character.",
                    "facade_module": "yuubot.core.facade",
                    "default_hints": {},
                },
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["data"]["system_prompt"] == "You are an E2E test character."

            get_resp = await client.get(
                "/api/resources/characters/char-e2e",
                headers=HEADERS,
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["data"]["system_prompt"] == "You are an E2E test character."

            update_resp = await client.put(
                "/api/resources/characters/char-e2e",
                headers=HEADERS,
                json={"system_prompt": "Updated prompt.", "name": "char-e2e"},
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["data"]["system_prompt"] == "Updated prompt."

            delete_resp = await client.delete(
                "/api/resources/characters/char-e2e",
                headers=HEADERS,
            )
            assert delete_resp.status_code == 200

            get_after = await client.get(
                "/api/resources/characters/char-e2e",
                headers=HEADERS,
            )
            assert get_after.status_code == 404
    finally:
        await runtime.services.stop()


async def test_create_capability_set(resources: Resources, tmp_path: Path) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/capability-sets",
                headers=HEADERS,
                json={
                    "id": "cap-e2e",
                    "name": "cap-e2e",
                    "integration_capability_ids": ["echo.send"],
                    "agent_tools": [],
                    "runtime_policy": {},
                    "resource_policy": {},
                },
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["data"]["integration_capability_ids"] == ["echo.send"]

            get_resp = await client.get(
                "/api/resources/capability-sets/cap-e2e",
                headers=HEADERS,
            )
            assert get_resp.status_code == 200
            assert "echo.send" in get_resp.json()["data"]["integration_capability_ids"]

            list_resp = await client.get(
                "/api/resources/capability-sets",
                headers=HEADERS,
            )
            assert list_resp.status_code == 200
            ids = [item["id"] for item in list_resp.json()["data"]]
            assert "cap-e2e" in ids

            delete_resp = await client.delete(
                "/api/resources/capability-sets/cap-e2e",
                headers=HEADERS,
            )
            assert delete_resp.status_code == 200
    finally:
        await runtime.services.stop()
