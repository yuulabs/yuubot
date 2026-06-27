"""Integration-to-actor smoke test for the runtime communication path."""

from __future__ import annotations

import pytest

from yuubot.core.actors import (
    ActorFactoryRegistry,
    ActorManager,
    ActorPythonSessionFactory,
    ActorWorkspaceResolver,
)
from yuubot.core.actors.impls.echo import ECHO_ACTOR_TYPE, EchoOnceActorFactory
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import (
    IntegrationCore,
    IntegrationFactoryRegistry,
)
from yuubot.core.integrations.impls.echo import (
    ECHO_CAPABILITY_ID,
    ECHO_INTEGRATION_NAME,
    EchoPayload,
    EchoIntegrationFactory,
)
from yuubot.core.routing import load_route_bindings
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    BudgetPolicy,
    CapabilitySetRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelConfig,
    Pricing,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CapabilitySetORM,
    IntegrationORM,
    LLMBackendORM,
)


async def test_test_integration_message_reaches_echo_actor(
    resources: Resources,
    tmp_path,
):
    repository = resources.repository
    source_path = "channels/test"
    integration = await _create_test_integration(repository, source_path)
    actor = await _create_actor_bundle(repository, "echo-actor")
    await _create_actor_ingress_rule(repository, integration.id, source_path, actor.id)

    gateway = Gateway(routes=await load_route_bindings(repository))
    integration_factory = EchoIntegrationFactory()
    integration_factories = IntegrationFactoryRegistry()
    integration_factories.register(integration_factory)
    integrations = IntegrationCore(
        repository=repository,
        factories=integration_factories,
        gateway=gateway,
        integrations_root=tmp_path / "data" / "integrations",
    )
    await integrations.enable(integration.id)

    python_sessions = ActorPythonSessionFactory.in_directory(
        integrations=integrations,
        root=tmp_path / "facades",
    )
    actor_factory = EchoOnceActorFactory(
        integrations=integrations,
        python_sessions=python_sessions,
    )
    actor_factories = ActorFactoryRegistry()
    actor_factories.register(actor_factory)
    actors = ActorManager(
        repository=repository,
        factories=actor_factories,
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(tmp_path / "workspaces"),
    )

    try:
        await actors.start_actor(actor.id)
        instance = integration_factory.instance(integration.id)

        await instance.send_to_channel(
            message_id="msg-1",
            sender_id="user-1",
            sender_name="Tester",
            kind="private",
            text="hello echo",
            content=[{"type": "text", "text": "hello echo"}],
        )

        expected = {"value": "hello echo"}
        assert await instance.next_echo_call() == EchoPayload(value="hello echo")
        context = await instance.next_echo_context()
        assert context["actor_id"] == actor.id
        assert context["raw"] == {}
        assert await actor_factory.actor(actor.id).next_echo_result() == expected
    finally:
        await actors.stop_actor(actor.id)
        await python_sessions.stop()
        await integrations.disable_all()


async def test_integration_invoke_requires_integration_id_when_capability_ambiguous(
    resources: Resources,
    tmp_path,
):
    repository = resources.repository
    actor = await _create_actor_bundle(
        repository,
        "echo-actor",
        integration_ids=("echo-a", "echo-b"),
    )
    await _create_test_integration(repository, "channels/a", integration_id="echo-a")
    await _create_test_integration(repository, "channels/b", integration_id="echo-b")

    gateway = Gateway(routes=await load_route_bindings(repository))
    integration_factory = EchoIntegrationFactory()
    integration_factories = IntegrationFactoryRegistry()
    integration_factories.register(integration_factory)
    integrations = IntegrationCore(
        repository=repository,
        factories=integration_factories,
        gateway=gateway,
        integrations_root=tmp_path / "data" / "integrations",
    )
    await integrations.enable_all()
    try:
        with pytest.raises(LookupError, match="ambiguous"):
            await integrations.invoke(
                actor_id=actor.id,
                capability_id=ECHO_CAPABILITY_ID,
                payload={"value": "ambiguous"},
            )

        result = await integrations.invoke(
            actor_id=actor.id,
            integration_id="echo-b",
            capability_id=ECHO_CAPABILITY_ID,
            payload={"value": "selected"},
        )

        assert result == EchoPayload(value="selected")
        echo_b = integration_factory.instance("echo-b")
        assert await echo_b.next_echo_call() == EchoPayload(value="selected")
        context = await echo_b.next_echo_context()
        assert context["integration_id"] == "echo-b"
    finally:
        await integrations.disable_all()


async def _create_test_integration(
    repository: ResourceRepository,
    source_path: str,
    *,
    integration_id: str = "test-integration",
) -> IntegrationRecord:
    return await repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id=integration_id,
            name=ECHO_INTEGRATION_NAME,
            config={"source_path": source_path},
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


async def _create_actor_bundle(
    repository: ResourceRepository,
    actor_id: str,
    *,
    integration_ids: tuple[str, ...] = ("test-integration",),
) -> ActorRecord:
    backend = await _create_llm_backend(repository, f"{actor_id}-backend")
    capability_set = await repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(
            id=f"{actor_id}-capabilities",
            name=f"{actor_id}-capabilities",
            integration_ids=integration_ids,
        ),
    )
    return await repository.insert(
        ActorORM,
        ActorRecord(
            id=actor_id,
            name=actor_id,
            type=ECHO_ACTOR_TYPE,
            persona_prompt="You echo messages.",
            capability_set_id=capability_set.id,
            llm_backend_id=backend.id,
            model="gpt-4",
        ),
    )


async def _create_llm_backend(
    repository: ResourceRepository,
    backend_id: str,
) -> LLMBackendRecord:
    return await repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id=backend_id,
            name=backend_id,
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
