"""Daemon resource command API tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
import msgspec
from starlette.types import ASGIApp

from yuubot.bootstrap.config import ServerConfig, TraceConfig
from yuubot.core.actors import Actor, ActorFactoryRegistry, ActorManager
from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.bindings import ActorBinding
from yuubot.core.capabilities import AnyCapability, AnyCapabilitySpec
from yuubot.core.gateway import Gateway, Mailbox
from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
from yuubot.core.integrations.contracts import IntegrationInstance, IntegrationStorage
from yuubot.core.secrets import Secret
from yuubot.core.routing import RouteBindings
from yuubot.process import ServiceHost, TraceService
from yuubot.resources.events import ResourceChanged
from yuubot.resources.records import (
    IntegrationRecord,
)
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


@dataclass
class FakeIntegrationInstance:
    closed: bool = False

    def capabilities(self) -> tuple[AnyCapability, ...]:
        return ()

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeIntegrationFactory:
    name: str = "fake"
    description: str = ""
    config_schema: dict[str, object] = field(default_factory=dict)
    instances: dict[str, FakeIntegrationInstance] = field(default_factory=dict)
    storage_dirs: dict[str, Path] = field(default_factory=dict)

    def capability_specs(self) -> tuple[AnyCapabilitySpec, ...]:
        return ()

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> IntegrationInstance:
        _ = gateway, storage
        instance = FakeIntegrationInstance()
        self.instances[record.id] = instance
        self.storage_dirs[record.id] = storage.data_dir
        return instance

    def routes(self, integrations: object) -> list:
        return []


class SecretFakeIntegrationConfig(msgspec.Struct, forbid_unknown_fields=False):
    bot_token: Secret
    label: str = ""


@dataclass
class SecretFakeIntegrationFactory(FakeIntegrationFactory):
    name: str = "secret-fake"
    config_schema: type[msgspec.Struct] = SecretFakeIntegrationConfig


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
    *,
    integration_factory: FakeIntegrationFactory | None = None,
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
    if integration_factory is not None:
        integration_factories.register(integration_factory)
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
    refresh = build_refresh_dispatcher(routes=routes, actors=actors, integrations=integrations)
    type_registry = build_default_resource_type_registry(
        integration_lifecycle_handler=_integration_lifecycle_handler(integrations),
        actor_lifecycle_handler=_actor_lifecycle_handler(actors),
    )
    trace_service = TraceService(config=TraceConfig(enabled=False), db_path=":memory:")
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


# --- Tests ---


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
        assert body["data"]["id"]  # auto-generated
    finally:
        await runtime.services.stop()


async def test_create_rejects_missing_secret(resources: Resources, tmp_path: Path) -> None:
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
                    "character": {"id": "nonexistent", "name": "x", "description": "", "system_prompt": "", "facade_module": "x", "default_hints": {}},
                    "llm_backend": {"id": "also-nonexistent", "name": "x", "yuuagents_provider": "openai", "model_capabilities": {}, "models": {}, "pricing": {}, "budget": {}},
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
        CharacterHints,
        CharacterRecord,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
    )
    from yuubot.resources.store.models import CharacterORM, LLMBackendORM

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
                    "character_id": character.id,
                    "llm_backend_id": backend.id,
                    "max_steps": 3,
                    "workspace_access": "read_write",
                    "capability_ids": ["echo.send"],
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["character"]["id"] == "char-simple"
        assert body["data"]["llm_backend"]["id"] == "backend-simple"
        assert body["data"]["budget"]["max_steps"] == 3
        assert body["data"]["resource_policy"]["workspace_access"] == "read_write"
        assert body["data"]["allowed_capability_ids"] == ["echo.send"]
    finally:
        await runtime.services.stop()


async def test_delete_referenced_llm_backend_returns_conflict(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import (
        BudgetPolicy,
        CharacterHints,
        CharacterRecord,
        LLMBackendRecord,
        ModelCapabilities,
        ModelCatalog,
        PricingTable,
        ResourcePolicy,
        RuntimePolicy,
        YuuAgentBudget,
        YuuAgentLLMOptions,
        ActorRecord,
    )
    from yuubot.resources.store.models import CharacterORM, LLMBackendORM, ActorORM

    repo = resources.repository
    character = await repo.insert(
        CharacterORM,
        CharacterRecord(
            id="char-1", name="char-1", description="", system_prompt="test", facade_module="x", default_hints=CharacterHints(),
        ),
    )
    backend = await repo.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="backend-1", name="backend-1", yuuagents_provider="openai",
            default_model="gpt-4", model_capabilities=ModelCapabilities(),
            models=ModelCatalog(), pricing=PricingTable(), budget=BudgetPolicy(),
        ),
    )
    await repo.insert(
        ActorORM,
        ActorRecord(
            id="actor-1", name="actor-1", type="fake",
            character=character, llm_backend=backend, model="",
            llm_options=YuuAgentLLMOptions(), budget=YuuAgentBudget(),
            agent_tools=(),
            allowed_capability_ids=(), runtime_policy=RuntimePolicy(),
            resource_policy=ResourcePolicy(),
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


async def test_integration_enable_disable_lifecycle(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.store.models import IntegrationORM

    repo = resources.repository
    await repo.insert(
        IntegrationORM,
        IntegrationRecord(
            id="int-1", name="fake", enabled=False,
        ),
    )

    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(resources, tmp_path, integration_factory=integration_factory)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/integrations/int-1/enable",
                headers=HEADERS,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert "integration.enabled" in body["actions"]
        assert runtime.integrations.running_integration_ids() == ["int-1"]

        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/integrations/int-1/disable",
                headers=HEADERS,
            )
        assert resp.status_code == 200, resp.text
        assert runtime.integrations.running_integration_ids() == []
    finally:
        await runtime.services.stop()


async def test_delete_integration_removes_private_storage(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.store.models import IntegrationORM

    repo = resources.repository
    await repo.insert(
        IntegrationORM,
        IntegrationRecord(id="int-delete", name="fake", enabled=True),
    )

    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(resources, tmp_path, integration_factory=integration_factory)
    await runtime.services.start()
    try:
        data_dir = integration_factory.storage_dirs["int-delete"]
        marker = data_dir / "cursor.txt"
        marker.write_text("42")
        assert marker.exists()

        async with _client(runtime) as client:
            resp = await client.delete(
                "/api/resources/integrations/int-delete",
                headers=HEADERS,
            )

        assert resp.status_code == 200, resp.text
        assert runtime.integrations.running_integration_ids() == []
        assert not data_dir.exists()
    finally:
        await runtime.services.stop()


async def test_integration_secret_config_is_encrypted_and_redacted(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.store.models import IntegrationORM

    runtime = _build_runtime(
        resources,
        tmp_path,
        integration_factory=SecretFakeIntegrationFactory(),
    )
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            created = await client.post(
                "/api/resources/integrations",
                headers=HEADERS,
                json={
                    "id": "secret-api",
                    "name": "secret-fake",
                    "config": {"bot_token": "plain-token", "label": "before"},
                },
            )
            updated = await client.put(
                "/api/resources/integrations/secret-api",
                headers=HEADERS,
                json={"config": {"bot_token": "", "label": "after"}},
            )

        assert created.status_code == 201, created.text
        assert created.json()["data"]["config"]["bot_token"] == "***"
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["config"]["bot_token"] == "***"

        with resources.store.db.activate():
            row = await IntegrationORM.get(id="secret-api")
        assert row.config["bot_token"]["$enc"] == "v1"
        assert "plain-token" not in repr(row.config)

        loaded = await resources.repository.get(IntegrationORM, "secret-api")
        assert loaded is not None
        token = loaded.config["bot_token"]
        assert isinstance(token, Secret)
        assert token.reveal() == "plain-token"
        assert loaded.config["label"] == "after"
    finally:
        await runtime.services.stop()


async def test_update_llm_backend(resources: Resources, tmp_path: Path) -> None:
    from yuubot.resources.records import (
        BudgetPolicy, LLMBackendRecord, ModelCapabilities, ModelCatalog, PricingTable,
    )
    from yuubot.resources.store.models import LLMBackendORM

    repo = resources.repository
    await repo.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="backend-u", name="backend-u", yuuagents_provider="openai",
            default_model="gpt-4", model_capabilities=ModelCapabilities(),
            models=ModelCatalog(), pricing=PricingTable(), budget=BudgetPolicy(),
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
        BudgetPolicy, LLMBackendRecord, ModelCapabilities, ModelCatalog, PricingTable,
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
        BudgetPolicy, LLMBackendRecord, ModelCapabilities, ModelCatalog, PricingTable,
    )
    from yuubot.resources.store.models import LLMBackendORM

    repo = resources.repository
    await repo.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="b1", name="b1", yuuagents_provider="openai",
            default_model="gpt-4", model_capabilities=ModelCapabilities(),
            models=ModelCatalog(), pricing=PricingTable(), budget=BudgetPolicy(),
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            get_resp = await client.get(
                "/api/resources/llm-backends/b1", headers=HEADERS,
            )
            list_resp = await client.get(
                "/api/resources/llm-backends", headers=HEADERS,
            )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == "b1"
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]) >= 1
    finally:
        await runtime.services.stop()


async def test_delete_nonexistent_returns_404(resources: Resources, tmp_path: Path) -> None:
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


async def test_unknown_resource_type_returns_404(resources: Resources, tmp_path: Path) -> None:
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
