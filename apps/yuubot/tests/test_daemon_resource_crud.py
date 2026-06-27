"""Daemon resource CRUD API tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
from starlette.types import ASGIApp

from yuubot.bootstrap.config import ServerConfig, TraceConfig
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
from yuubot.resources.records import (
    BudgetPolicy,
    LLMBackendRecord,
    ModelCapabilities,
    ModelConfig,
    Pricing,
)
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


def _client(runtime: RuntimeHarness) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.app),
        base_url="http://testserver",
    )


def _backend_payload(
    *,
    name: str,
    provider_identity: str = "openai",
    model: str = "gpt-4",
) -> dict[str, object]:
    return {
        "name": name,
        "provider_identity": provider_identity,
        "provider_options": {"api_key": "sk-test"},
        "model_configs": {
            model: {
                "pricing": {
                    "input_per_million": 1.25,
                    "cached_input_per_million": 0.25,
                    "output_per_million": 2.5,
                },
                "capabilities": {"chat": True, "tool_calling": True},
            }
        },
        "budget": {},
    }


def _backend_record(
    backend_id: str,
    *,
    provider_identity: str = "openai",
    model: str = "gpt-4",
) -> LLMBackendRecord:
    return LLMBackendRecord(
        id=backend_id,
        name=backend_id,
        provider_identity=provider_identity,
        model_configs={
            model: ModelConfig(
                pricing=Pricing(
                    input_per_million=1.25,
                    cached_input_per_million=0.25,
                    output_per_million=2.5,
                ),
                capabilities=ModelCapabilities(chat=True, tool_calling=True),
            )
        },
        budget=BudgetPolicy(),
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
                    **_backend_payload(name="test-backend"),
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["name"] == "test-backend"
        assert body["data"]["id"]
    finally:
        await runtime.services.stop()


async def test_create_llm_backend_preserves_model_configs(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/llm-backends",
                headers=HEADERS,
                json={
                    **_backend_payload(name="openai-backend"),
                },
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["provider_identity"] == "openai"
        assert data["model_configs"]["gpt-4"]["pricing"] == {
            "input_per_million": 1.25,
            "cached_input_per_million": 0.25,
            "output_per_million": 2.5,
        }
        assert data["model_configs"]["gpt-4"]["capabilities"]["tool_calling"] is True
    finally:
        await runtime.services.stop()


async def test_create_deepseek_llm_backend_uses_provider_identity(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/llm-backends",
                headers=HEADERS,
                json={
                    **_backend_payload(
                        name="deepseek-backend",
                        provider_identity="deepseek",
                        model="deepseek-chat",
                    ),
                },
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["provider_identity"] == "deepseek"
        assert set(data["model_configs"]) == {"deepseek-chat"}
    finally:
        await runtime.services.stop()


async def test_create_llm_backend_encrypts_api_key_at_rest(
    resources: Resources, tmp_path: Path
) -> None:
    """ISSUE-0005 §2.2: provider api_key must be encrypted at rest.

    A plaintext ``api_key`` entering the admin create boundary is wrapped into
    a ``Secret`` (via ``secret_decode_hook``) and encrypted by the
    ``SecretCodec`` at the DB write boundary (``secret_enc_hook``), landing as
    ``{"$enc":"v1","ct":...}`` in the ``provider_options`` JSON column — never
    as plaintext. The repository read boundary (``secret_dec_hook``) decrypts it
    back to a revealable ``Secret``. This mirrors the integration-side secret
    persistence pattern end to end.
    """
    from yuubot.core.secrets import Secret
    from yuubot.resources.store.models import LLMBackendORM

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/llm-backends",
                headers=HEADERS,
                json={**_backend_payload(name="secret-backend")},
            )
        assert resp.status_code == 201, resp.text
        backend_id = resp.json()["data"]["id"]

        # The API response redacts—plaintext never leaks out.
        assert resp.json()["data"]["provider_options"]["api_key"] == "***"

        # The DB column stores an encrypted secret, not the plaintext.
        with resources.store.db.activate():
            row = await LLMBackendORM.get(id=backend_id)
        stored = row.provider_options
        assert stored["api_key"]["$enc"] == "v1"
        assert "ct" in stored["api_key"]
        assert "sk-test" not in repr(stored)

        # Round-trip: repository read decrypts back to a revealable Secret.
        loaded = await resources.repository.get(LLMBackendORM, backend_id)
        assert loaded is not None
        assert isinstance(loaded.provider_options.api_key, Secret)
        assert loaded.provider_options.api_key.reveal() == "sk-test"
    finally:
        await runtime.services.stop()


async def test_create_unknown_provider_identity_rejected(
    resources: Resources, tmp_path: Path
) -> None:
    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.post(
                "/api/resources/llm-backends",
                headers=HEADERS,
                json={
                    **_backend_payload(
                        name="custom-backend",
                        provider_identity="custom",
                        model="custom-model",
                    ),
                },
            )
        assert resp.status_code == 400, resp.text
        assert "unknown provider_identity" in resp.json()["detail"]
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
                json={"name": "x", "provider_identity": "openai"},
            )
        assert resp.status_code == 403
    finally:
        await runtime.services.stop()


async def test_create_actor_validates_referenced_resources(
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
                    "persona_prompt": "test",
                    "capability_set_id": "missing-capability-set",
                    "llm_backend_id": "missing-backend",
                    "model": "gpt-4",
                },
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "validation_error"
        assert "capability_set 'missing-capability-set' not found" in body["detail"]
    finally:
        await runtime.services.stop()


async def test_create_actor_accepts_typed_simplified_request(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import CapabilitySetRecord
    from yuubot.resources.store.models import (
        CapabilitySetORM,
        LLMBackendORM,
    )

    backend = await resources.repository.insert(
        LLMBackendORM,
        _backend_record("backend-simple"),
    )
    capability_set = await resources.repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(
            id="cap-simple",
            name="cap-simple",
            integration_ids=("echo-main",),
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
                    "persona_prompt": "test",
                    "capability_set_id": capability_set.id,
                    "llm_backend_id": backend.id,
                    "model": "gpt-4",
                    "per_run_budget": {"max_steps": 3},
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["persona_prompt"] == "test"
        assert body["data"]["capability_set_id"] == "cap-simple"
        assert body["data"]["llm_backend_id"] == "backend-simple"
        assert body["data"]["model"] == "gpt-4"
        assert body["data"]["per_run_budget"]["max_steps"] == 3
    finally:
        await runtime.services.stop()


async def test_update_actor_rejects_unconfigured_model(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import ActorRecord, CapabilitySetRecord
    from yuubot.resources.store.models import (
        ActorORM,
        CapabilitySetORM,
        LLMBackendORM,
    )

    backend = await resources.repository.insert(
        LLMBackendORM,
        _backend_record("backend-model-check"),
    )
    capability_set = await resources.repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(id="cap-model-check", name="cap-model-check"),
    )
    await resources.repository.insert(
        ActorORM,
        ActorRecord(
            id="actor-model-check",
            name="actor-model-check",
            type="fake",
            persona_prompt="test",
            capability_set_id=capability_set.id,
            llm_backend_id=backend.id,
            model="gpt-4",
        ),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.put(
                "/api/resources/actors/actor-model-check",
                headers=HEADERS,
                json={"model": "missing-model"},
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "configuration_error"
        assert "model 'missing-model' is not configured" in body["detail"]
    finally:
        await runtime.services.stop()


async def test_delete_referenced_llm_backend_returns_conflict(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.records import (
        ActorRecord,
        CapabilitySetRecord,
    )
    from yuubot.resources.store.models import (
        ActorORM,
        CapabilitySetORM,
        LLMBackendORM,
    )

    repo = resources.repository
    backend = await repo.insert(
        LLMBackendORM,
        _backend_record("backend-1"),
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
            persona_prompt="test",
            capability_set_id=capability_set.id,
            llm_backend_id=backend.id,
            model="gpt-4",
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
    from yuubot.resources.store.models import LLMBackendORM

    repo = resources.repository
    await repo.insert(
        LLMBackendORM,
        _backend_record("backend-u"),
    )

    runtime = _build_runtime(resources, tmp_path)
    await runtime.services.start()
    try:
        async with _client(runtime) as client:
            resp = await client.put(
                "/api/resources/llm-backends/backend-u",
                headers=HEADERS,
                json={"default_generation_params": {"temperature": 0.2}},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["default_generation_params"]["temperature"] == 0.2
    finally:
        await runtime.services.stop()


async def test_update_llm_backend_rejects_unknown_field(
    resources: Resources, tmp_path: Path
) -> None:
    from yuubot.resources.store.models import LLMBackendORM

    await resources.repository.insert(
        LLMBackendORM,
        _backend_record("backend-schema"),
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
    from yuubot.resources.store.models import LLMBackendORM

    repo = resources.repository
    await repo.insert(
        LLMBackendORM,
        _backend_record("b1"),
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
