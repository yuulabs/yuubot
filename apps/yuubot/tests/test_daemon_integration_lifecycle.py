"""Daemon integration lifecycle API tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
import msgspec
from starlette.types import ASGIApp

from yuubot.bootstrap.config import ServerConfig, TraceConfig
from yuubot.core.actors import Actor, ActorFactoryRegistry, ActorManager
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.assembly import llm_session_factory_for_binding
from yuubot.core.bindings import ActorBinding
from yuubot.core.capabilities import AnyCapability, AnyCapabilitySpec
from yuubot.core.facade import FacadeWorkspace, IntegrationInvokeBridge
from yuubot.core.gateway import Gateway, Mailbox
from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
from yuubot.core.integrations import default_integration_factories
from yuubot.core.integrations.contracts import IntegrationInstance, IntegrationStorage
from yuubot.core.routing import RouteBindings
from yuubot.process import ServiceHost, TraceService
from yuubot.resources.events import ResourceChanged
from yuubot.resources.records import IntegrationRecord
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
from yuubot.core.secrets import Secret

SECRET = "test-secret"
HEADERS = {"X-Daemon-Secret": SECRET}


# --- Fakes ---


@dataclass
class FakeActor:
    binding: ActorBinding

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        _ = event

    async def handle_message(self, message) -> None:
        _ = message


@dataclass
class FakeActorFactory:
    actor_type: str = "fake"

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        _ = mailbox
        return FakeActor(binding)


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

    @property
    def sdk_spec(self):
        from yuubot.core.integrations.contracts import IntegrationSdkSpec

        return IntegrationSdkSpec()

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
    integration_factories: IntegrationFactoryRegistry | None = None,
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
    integration_factories = integration_factories or IntegrationFactoryRegistry()
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
    refresh = build_refresh_dispatcher(
        routes=routes, actors=actors, integrations=integrations
    )
    type_registry = build_default_resource_type_registry(
        integration_lifecycle_handler=_integration_lifecycle_handler(integrations),
        actor_lifecycle_handler=_actor_lifecycle_handler(actors),
    )
    trace_service = TraceService(
        config=TraceConfig(
            enabled=False,
            collector_host="127.0.0.1",
            collector_port=4318,
        ),
        db_path=":memory:",
    )
    python_sessions = ActorPythonSessionFactory(
        integrations=integrations,
        workspace=FacadeWorkspace(workspace_root / "facades"),
        bridge=IntegrationInvokeBridge(integrations),
    )
    app = build_daemon_asgi_app(
        config=ServerConfig(
            daemon_host="127.0.0.1",
            daemon_port=8780,
            daemon_secret=SECRET,
        ),
        resources=resources,
        services=services,
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        refresh=refresh,
        trace_service=trace_service,
        type_registry=type_registry,
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


def _client(
    runtime: RuntimeHarness,
    *,
    raise_app_exceptions: bool = True,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=runtime.app,
            raise_app_exceptions=raise_app_exceptions,
        ),
        base_url="http://testserver",
    )


# --- Integration Lifecycle Tests ---


async def test_integration_enable_disable_lifecycle(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.store.models import IntegrationORM

    repo = resources.repository
    await repo.insert(
        IntegrationORM,
        IntegrationRecord(
            id="int-1",
            name="fake",
            enabled=False,
        ),
    )

    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(
        resources, tmp_path, integration_factory=integration_factory
    )
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
    runtime = _build_runtime(
        resources, tmp_path, integration_factory=integration_factory
    )
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


async def test_create_integration_defaults_to_disabled(
    resources: Resources, tmp_path: Path
) -> None:
    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(
        resources, tmp_path, integration_factory=integration_factory
    )
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/integrations",
                headers=HEADERS,
                json={
                    "id": "api-disabled",
                    "name": "fake",
                    "config": {},
                },
            )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["enabled"] is False
        assert runtime.integrations.running_integration_ids() == []
    finally:
        await runtime.services.stop()


async def test_create_integration_with_enabled_true_starts_runtime(
    resources: Resources, tmp_path: Path
) -> None:
    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(
        resources, tmp_path, integration_factory=integration_factory
    )
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/integrations",
                headers=HEADERS,
                json={
                    "id": "api-enabled",
                    "name": "fake",
                    "config": {},
                    "enabled": True,
                },
            )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["enabled"] is True
        assert runtime.integrations.running_integration_ids() == ["api-enabled"]
    finally:
        await runtime.services.stop()


async def test_enabling_github_without_pat_returns_validation_error(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.store.models import IntegrationORM

    repo = resources.repository
    await repo.insert(
        IntegrationORM,
        IntegrationRecord(
            id="github-empty",
            name="github",
            config={},
            enabled=False,
        ),
    )

    runtime = _build_runtime(
        resources,
        tmp_path,
        integration_factories=default_integration_factories(),
    )

    await runtime.services.start()
    try:
        async with _client(runtime, raise_app_exceptions=False) as client:
            resp = await client.post(
                "/api/resources/integrations/github-empty/enable",
                headers=HEADERS,
            )

        assert resp.status_code >= 400, resp.text
        assert runtime.integrations.running_integration_ids() == []
    finally:
        await runtime.services.stop()
