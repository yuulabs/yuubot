"""Daemon refresh API and runtime reconcile behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
from starlette.types import ASGIApp

from yuubot.bootstrap.config import ServerConfig, TraceConfig
from yuubot.core.actors import Actor, ActorFactoryRegistry, ActorManager
from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.assembly import llm_session_factory_for_binding
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import FacadeWorkspace, IntegrationInvokeBridge
from yuubot.core.capabilities import AnyCapability, AnyCapabilitySpec
from yuubot.core.gateway import Gateway, Mailbox
from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
from yuubot.core.integrations.contracts import IntegrationInstance, IntegrationStorage
from yuubot.core.messages import IncomingMessage, MessageSource
from yuubot.core.routing import RouteBindings
from yuubot.process import ServiceHost, TraceService
from yuubot.resources.events import ResourceChanged
from yuubot.resources.records import (
    ActorRecord,
    ActorIngressRuleRecord,
    BudgetPolicy,
    CapabilitySetRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelConfig,
    Pricing,
    RunBudget,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import (
    ActorORM,
    ActorIngressRuleORM,
    CapabilitySetORM,
    IntegrationORM,
    LLMBackendORM,
)
from yuubot.runtime.daemon.commands import build_default_resource_type_registry
from yuubot.runtime.daemon import (
    ActorLifecycleService,
    IntegrationLifecycleService,
    RouteBindingService,
    build_daemon_asgi_app,
    build_refresh_dispatcher,
)


async def test_resource_changed_api_schema_round_trips() -> None:
    event = ResourceChanged(
        table="actor_ingress_rules",
        action="inserted",
        row_ids=("slack-main:channels/dev:actor-main",),
        changed_fields=(),
    )

    payload = event.to_dict()
    parsed = ResourceChanged.from_dict(payload)

    assert payload == {
        "type": "resource.changed",
        "table": "actor_ingress_rules",
        "action": "inserted",
        "row_ids": ["slack-main:channels/dev:actor-main"],
        "changed_fields": [],
    }
    assert parsed == event


async def test_refresh_rejects_missing_or_bad_daemon_secret(
    resources: Resources,
    tmp_path: Path,
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            event = ResourceChanged(
                table="actor_ingress_rules",
                action="inserted",
                row_ids=("slack-main:channels/dev:actor-main",),
            )

            missing = await client.post("/api/admin/refresh", json=event.to_dict())
            wrong = await client.post(
                "/api/admin/refresh",
                headers={"X-Daemon-Secret": "wrong"},
                json=event.to_dict(),
            )

        assert missing.status_code == 403
        assert wrong.status_code == 403
    finally:
        await runtime.services.stop()


async def test_daemon_start_restores_runtime_status(
    resources: Resources,
    tmp_path: Path,
) -> None:
    repository = resources.repository
    integration = await _create_integration(repository, enabled=True)
    actor = await _create_actor_bundle(repository, "actor-main")
    await _create_actor_ingress_rule(repository, "slack-main", "channels/dev", actor.id)

    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(
        resources,
        tmp_path,
        integration_factory=integration_factory,
        trace_enabled=False,
    )
    await runtime.services.start()
    try:
        # Actors require explicit start — daemon boot does not auto-start.
        await runtime.actors.start_actor(actor.id)

        async with _client(runtime) as client:
            response = await client.get(
                "/api/status",
                headers={"X-Daemon-Secret": "secret"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "status": "running",
            "running_integration_ids": [integration.id],
            "running_actor_ids": [actor.id],
            "actor_workspaces": runtime.actors.running_actor_workspace_paths(),
            "route_binding_count": 2,
            "trace": {"enabled": False, "status": "disabled"},
        }
        assert integration_factory.instances[integration.id].closed is False
    finally:
        await runtime.services.stop()


async def test_refresh_actor_ingress_rule_reloads_routes(
    resources: Resources,
    tmp_path: Path,
) -> None:
    repository = resources.repository
    actor = await _create_actor_bundle(repository, "actor-main")
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        rule = await _create_actor_ingress_rule(
            repository,
            "slack-main",
            "channels/dev",
            actor.id,
        )

        async with _client(runtime) as client:
            response = await client.post(
                "/api/admin/refresh",
                headers={"X-Daemon-Secret": "secret"},
                json=ResourceChanged(
                    table="actor_ingress_rules",
                    action="inserted",
                    row_ids=(rule.id,),
                ).to_dict(),
            )

        assert response.status_code == 200
        assert response.json()["actions"] == ["routes.reloaded"]
        assert runtime.gateway.routes.resolve(
            _message("slack-main", "channels/dev")
        ) == [
            actor.id,
        ]
    finally:
        await runtime.services.stop()


async def test_refresh_integrations_reconciles_enabled_state(
    resources: Resources,
    tmp_path: Path,
) -> None:
    repository = resources.repository
    integration = await _create_integration(repository, enabled=False)
    integration_factory = FakeIntegrationFactory()
    runtime = _build_runtime(
        resources,
        tmp_path,
        integration_factory=integration_factory,
    )
    await runtime.services.start()
    try:
        await repository.update(IntegrationORM, integration.id, enabled=True)

        async with _client(runtime) as client:
            enabled = await client.post(
                "/api/admin/refresh",
                headers={"X-Daemon-Secret": "secret"},
                json=ResourceChanged(
                    table="integrations",
                    action="updated",
                    row_ids=(integration.id,),
                    changed_fields=("enabled",),
                ).to_dict(),
            )

        assert enabled.status_code == 200
        assert runtime.integrations.running_integration_ids() == [integration.id]

        instance = integration_factory.instances[integration.id]
        await repository.update(IntegrationORM, integration.id, enabled=False)
        async with _client(runtime) as client:
            disabled = await client.post(
                "/api/admin/refresh",
                headers={"X-Daemon-Secret": "secret"},
                json=ResourceChanged(
                    table="integrations",
                    action="updated",
                    row_ids=(integration.id,),
                    changed_fields=("enabled",),
                ).to_dict(),
            )

        assert disabled.status_code == 200
        assert runtime.integrations.running_integration_ids() == []
        assert instance.closed is True
    finally:
        await runtime.services.stop()


@dataclass
class RuntimeHarness:
    actors: ActorManager
    integrations: IntegrationCore
    gateway: Gateway
    services: ServiceHost
    app: ASGIApp


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

    async def handle_message(self, message: IncomingMessage) -> None:
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
        return instance

    def routes(self, integrations: object) -> list:
        return []


def _build_runtime(
    resources: Resources,
    workspace_root: Path,
    *,
    integration_factory: FakeIntegrationFactory | None = None,
    trace_enabled: bool = True,
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
    refresh = build_refresh_dispatcher(
        routes=routes,
        actors=actors,
        integrations=integrations,
    )
    type_registry = build_default_resource_type_registry()
    trace_service = TraceService(
        config=TraceConfig(
            enabled=trace_enabled,
            collector_host="127.0.0.1",
            collector_port=4318,
        ),
        db_path=":memory:",
    )
    app = build_daemon_asgi_app(
        config=ServerConfig(
            daemon_host="127.0.0.1",
            daemon_port=8780,
            daemon_secret="secret",
        ),
        resources=resources,
        services=services,
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        refresh=refresh,
        trace_service=trace_service,
        type_registry=type_registry,
        python_sessions=ActorPythonSessionFactory(
            integrations=integrations,
            workspace=FacadeWorkspace(workspace_root / "facades"),
            bridge=IntegrationInvokeBridge(integrations),
        ),
        llm_session_factory_factory=llm_session_factory_for_binding,
    )
    return RuntimeHarness(
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        services=services,
        app=app,
    )


def _client(runtime: RuntimeHarness) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.app),
        base_url="http://testserver",
    )


async def _create_integration(
    repository: ResourceRepository,
    *,
    enabled: bool,
) -> IntegrationRecord:
    return await repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="integration-main",
            name="fake",
            enabled=enabled,
        ),
    )


async def _create_actor_bundle(
    repository: ResourceRepository,
    actor_id: str,
) -> ActorRecord:
    backend = await repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id=f"{actor_id}-backend",
            name=f"{actor_id}-backend",
            provider_identity="openai",
            model_configs={
                "gpt-4": ModelConfig(
                    pricing=Pricing(),
                    capabilities=ModelCapabilities(),
                )
            },
            budget=BudgetPolicy(),
        ),
    )
    capability_set = await repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(
            id=f"{actor_id}-capabilities",
            name=f"{actor_id}-capabilities",
        ),
    )
    return await repository.insert(
        ActorORM,
        ActorRecord(
            id=actor_id,
            name=actor_id,
            type="fake",
            persona_prompt="You are test.",
            capability_set_id=capability_set.id,
            llm_backend_id=backend.id,
            model="gpt-4",
            per_run_budget=RunBudget(),
        ),
    )


async def _create_actor_ingress_rule(
    repository: ResourceRepository,
    source_id_pattern: str,
    source_path_pattern: str,
    actor_id: str,
) -> ActorIngressRuleRecord:
    return await repository.insert(
        ActorIngressRuleORM,
        ActorIngressRuleRecord(
            id=f"{source_id_pattern}:{source_path_pattern}:{actor_id}",
            actor_id=actor_id,
            source_id_pattern=source_id_pattern,
            source_path_pattern=source_path_pattern,
        ),
    )


def _message(source_id: str, source_path: str) -> IncomingMessage:
    return IncomingMessage(
        message_id="msg-1",
        sender_id="user-1",
        source=MessageSource(id=source_id, path=source_path),
        content=[{"type": "text", "text": "hello"}],
    )
