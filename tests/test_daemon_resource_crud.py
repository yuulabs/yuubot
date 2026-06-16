"""Daemon resource CRUD API tests."""

from __future__ import annotations

from dataclasses import dataclass, field
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
from yuubot.resources.registry import EventDrivenRefreshDispatcher, ResourceTypeRegistry
from yuubot.runtime.daemon.commands import build_default_resource_type_registry
from yuubot.runtime.daemon import (
    ActorLifecycleService,
    IntegrationLifecycleService,
    RouteBindingService,
    _actor_lifecycle_handler,
    _integration_lifecycle_handler,
    build_daemon_asgi_app,
    build_refresh_dispatcher,
)

SECRET = "test-secret"
HEADERS = {"X-Daemon-Secret": SECRET}


# --- Fakes ---


@dataclass
class FakeActor:
    binding: ActorBinding
    started: bool = False

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        _ = event

    async def handle_message(self, message) -> None:
        _ = message


@dataclass
class FakeActorFactory:
    actor_type: str = "fake"
    actors: dict[str, FakeActor] = field(default_factory=dict)

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        _ = mailbox
        actor = FakeActor(binding)
        self.actors[binding.actor.id] = actor
        return actor


# --- Harness ---


@dataclass
class RuntimeHarness:
    actors: ActorManager
    integrations: IntegrationCore
    gateway: Gateway
    services: ServiceHost
    app: ASGIApp
    refresh: EventDrivenRefreshDispatcher
    type_registry: ResourceTypeRegistry


def _build_runtime(
    resources: Resources,
    workspace_root: Path,
) -> RuntimeHarness:
    gateway = Gateway(routes=RouteBindings(rules=()))
    actor_factories = ActorFactoryRegistry()
    actor_factories.register(FakeActorFactory())
    actors = ActorManager(
        repository=resources.repository,
        factories=actor_factories,
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(workspace_root / "workspaces"),
    )
    integration_factories = IntegrationFactoryRegistry()
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=integration_factories,
        gateway=gateway,
        integrations_root=workspace_root / "data" / "integrations",
    )
    routes = RouteBindingService(repository=resources.repository, gateway=gateway)
    services = ServiceHost.from_iterable(
        (
            IntegrationLifecycleService(integrations),
            routes,
            ActorLifecycleService(actors),
        )
    )
    refresh = build_refresh_dispatcher(
        routes=routes, actors=actors, integrations=integrations
    )
    type_registry = build_default_resource_type_registry(
        integration_lifecycle_handler=_integration_lifecycle_handler(integrations),
        actor_lifecycle_handler=_actor_lifecycle_handler(actors),
    )
    trace_service = TraceService(config=TraceConfig(enabled=False), db_path=":memory:")
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
    return RuntimeHarness(
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        services=services,
        app=app,
        refresh=refresh,
        type_registry=type_registry,
    )


def _client(runtime: RuntimeHarness) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.app),
        base_url="http://testserver",
    )


# --- CRUD Tests ---


async def test_create_llm_backend(resources: Resources, tmp_path: Path) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/llm-backends",
                headers=HEADERS,
                json={
                    "name": "test-backend",
                    "yuuagents_provider": "openai",
                    "model_capabilities": {"chat": True},
                    "models": {"names": []},
                    "pricing": {"entries": []},
                    "budget": {},
                    "default_model": "gpt-4",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["name"] == "test-backend"
        assert body["data"]["id"]
    finally:
        await runtime.services.stop()


async def test_create_rejects_missing_secret(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/llm-backends",
                json={"name": "x", "yuuagents_provider": "openai"},
            )
        assert resp.status_code == 403
    finally:
        await runtime.services.stop()


async def test_create_actor_validates_character_reference(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/actors",
                headers=HEADERS,
                json={
                    "name": "bad-actor",
                    "type": "fake",
                    "character": {
                        "id": "nonexistent",
                        "name": "x",
                        "description": "",
                        "system_prompt": "",
                        "facade_module": "x",
                        "default_hints": {},
                    },
                    "llm_backend": {
                        "id": "also-nonexistent",
                        "name": "x",
                        "yuuagents_provider": "openai",
                        "model_capabilities": {},
                        "models": {},
                        "pricing": {},
                        "budget": {},
                    },
                    "model": "",
                    "llm_options": {},
                    "budget": {},
                    "agent_tools": [],
                    "allowed_capability_ids": [],
                    "runtime_policy": {},
                    "resource_policy": {},
                },
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "validation_error"
        assert "character" in body["detail"]
    finally:
        await runtime.services.stop()


async def test_create_actor_accepts_typed_simplified_request(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import (
        BudgetPolicy,
        CapabilitySetRecord,
        CharacterHints,
        CharacterRecord,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
    )
    from yuubot.resources.store.models import (
        CapabilitySetORM,
        CharacterORM,
        LLMBackendORM,
    )

    character = await resources.repository.insert(
        CharacterORM,
        CharacterRecord(
            id="char-simple",
            name="char-simple",
            description="",
            system_prompt="test",
            facade_module="x",
            default_hints=CharacterHints(),
        ),
    )
    backend = await resources.repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="backend-simple",
            name="backend-simple",
            yuuagents_provider="openai",
            default_model="gpt-4",
            model_capabilities=ModelCapabilities(),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )
    capability_set = await resources.repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(
            id="cap-simple",
            name="cap-simple",
            integration_capability_ids=("echo.send",),
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/actors",
                headers=HEADERS,
                json={
                    "name": "simple-actor",
                    "type": "fake",
                    "default_character_id": character.id,
                    "capability_set_id": capability_set.id,
                    "default_llm_backend_id": backend.id,
                    "default_budget": {"max_steps": 3},
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["default_character"]["id"] == "char-simple"
        assert body["data"]["capability_set"]["id"] == "cap-simple"
        assert body["data"]["default_llm_backend"]["id"] == "backend-simple"
        assert body["data"]["default_budget"]["max_steps"] == 3
    finally:
        await runtime.services.stop()


async def test_delete_referenced_llm_backend_returns_conflict(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import (
        BudgetPolicy,
        CapabilitySetRecord,
        CharacterHints,
        CharacterRecord,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
        ActorRecord,
    )
    from yuubot.resources.store.models import (
        ActorORM,
        CapabilitySetORM,
        CharacterORM,
        LLMBackendORM,
    )

    repo = resources.repository
    character = await repo.insert(
        CharacterORM,
        CharacterRecord(
            id="char-1",
            name="char-1",
            description="",
            system_prompt="test",
            facade_module="x",
            default_hints=CharacterHints(),
        ),
    )
    backend = await repo.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="backend-1",
            name="backend-1",
            yuuagents_provider="openai",
            default_model="gpt-4",
            model_capabilities=ModelCapabilities(),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )
    capability_set = await repo.insert(
        CapabilitySetORM,
        CapabilitySetRecord(id="cap-1", name="cap-1"),
    )
    await repo.insert(
        ActorORM,
        ActorRecord(
            id="actor-1",
            name="actor-1",
            type="fake",
            default_character=character,
            capability_set=capability_set,
            default_llm_backend=backend,
            default_model="",
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.delete(
                "/api/resources/llm-backends/backend-1",
                headers=HEADERS,
            )
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "conflict"
    finally:
        await runtime.services.stop()


async def test_update_llm_backend(resources: Resources, tmp_path: Path) -> None:
    from yuubot.resources.records import (
        BudgetPolicy,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
    )
    from yuubot.resources.store.models import LLMBackendORM

    repo = resources.repository
    await repo.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="backend-u",
            name="backend-u",
            yuuagents_provider="openai",
            default_model="gpt-4",
            model_capabilities=ModelCapabilities(),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.put(
                "/api/resources/llm-backends/backend-u",
                headers=HEADERS,
                json={"default_model": "gpt-4o"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["default_model"] == "gpt-4o"
    finally:
        await runtime.services.stop()


async def test_update_llm_backend_rejects_unknown_field(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import (
        BudgetPolicy,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
    )
    from yuubot.resources.store.models import LLMBackendORM

    await resources.repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="backend-schema",
            name="backend-schema",
            yuuagents_provider="openai",
            default_model="gpt-4",
            model_capabilities=ModelCapabilities(),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.put(
                "/api/resources/llm-backends/backend-schema",
                headers=HEADERS,
                json={"defualt_model": "typo"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "validation_error"
        assert "unknown field" in resp.json()["detail"]
    finally:
        await runtime.services.stop()


async def test_get_and_list_resources(resources: Resources, tmp_path: Path) -> None:
    from yuubot.resources.records import (
        BudgetPolicy,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
    )
    from yuubot.resources.store.models import LLMBackendORM

    repo = resources.repository
    await repo.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="b1",
            name="b1",
            yuuagents_provider="openai",
            default_model="gpt-4",
            model_capabilities=ModelCapabilities(),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            get_resp = await client.get(
                "/api/resources/llm-backends/b1",
                headers=HEADERS,
            )
            list_resp = await client.get(
                "/api/resources/llm-backends",
                headers=HEADERS,
            )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == "b1"
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]) >= 1
    finally:
        await runtime.services.stop()


async def test_delete_nonexistent_returns_404(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.delete(
                "/api/resources/llm-backends/nope",
                headers=HEADERS,
            )
        assert resp.status_code == 404
    finally:
        await runtime.services.stop()


async def test_unknown_resource_type_returns_404(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/unknown-type",
                headers=HEADERS,
                json={"name": "x"},
            )
        assert resp.status_code == 404
    finally:
        await runtime.services.stop()



