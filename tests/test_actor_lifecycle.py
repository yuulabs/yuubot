"""Actor lifecycle starts only from lifecycle commands and dispatches by type."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yuubot.core.actors import (
    Actor,
    ActorFactoryRegistry,
    ActorManager,
    ActorWorkspaceResolver,
    StartActor,
)
from yuubot.core.bindings import ActorBinding
from yuubot.core.gateway import Gateway, Mailbox
from yuubot.core.messages import IncomingMessage
from yuubot.core.routing import ActorIngressRule, RouteBindings
from yuubot.resources.events import ResourceChanged
from yuubot.resources.records import (
    ActorRecord,
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
)
from yuubot.resources.root import Resources
from yuubot.resources.store.models import (
    ActorORM,
    CharacterORM,
    LLMBackendORM,
)


async def test_actor_insert_does_not_start_actor(resources: Resources, tmp_path: Path):
    registry = ActorFactoryRegistry()
    registry.register(FakeActorFactory())
    manager = _actor_manager(resources, registry, tmp_path)

    await _insert_actor_bundle(resources, "test-actor", actor_type="fake")

    assert manager.running_actor("test-actor") is None


async def test_start_actor_command_uses_actor_type_factory(
    resources: Resources,
    tmp_path: Path,
):
    factory = FakeActorFactory()
    registry = ActorFactoryRegistry()
    registry.register(factory)
    manager = _actor_manager(resources, registry, tmp_path)
    actor_record = await _insert_actor_bundle(
        resources,
        "typed-actor",
        actor_type="fake",
    )

    await manager.handle_lifecycle_command(StartActor(actor_record.id))

    actor = manager.running_actor(actor_record.id)
    assert isinstance(actor, FakeActor)
    assert actor.started is True
    assert actor.binding.character.system_prompt == "You are test"
    assert factory.started_actor_types == ["fake"]


async def test_running_actor_receives_relevant_resource_events(
    resources: Resources,
    tmp_path: Path,
):
    registry = ActorFactoryRegistry()
    registry.register(FakeActorFactory())
    manager = _actor_manager(resources, registry, tmp_path)
    actor_record = await _insert_actor_bundle(resources, "listener", actor_type="fake")
    await manager.handle_lifecycle_command(StartActor(actor_record.id))

    event = ResourceChanged(table="agents", action="updated", row_ids=("agent-1",))
    await manager.forward_resource_change(event)

    actor = manager.running_actor(actor_record.id)
    assert isinstance(actor, FakeActor)
    assert actor.events == [event]


async def test_start_actor_raises_on_failure(
    resources: Resources,
    tmp_path: Path,
):
    registry = ActorFactoryRegistry()
    registry.register(FakeActorFactory(failing_actor_ids={"broken"}))
    gateway = Gateway(
        routes=RouteBindings(
            rules=[
                ActorIngressRule(
                    actor_id="broken",
                    source_id_pattern="*",
                    source_path_pattern="**",
                    kind_patterns=["*"],
                ),
            ]
        )
    )
    manager = ActorManager(
        repository=resources.repository,
        factories=registry,
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(tmp_path / "workspaces"),
    )
    await _insert_actor_bundle(resources, "broken", actor_type="fake")

    with pytest.raises(RuntimeError, match="boom"):
        await manager.start_actor("broken")

    assert manager.running_actor_ids() == []


@dataclass
class FakeActor:
    binding: ActorBinding
    started: bool = False
    events: list[ResourceChanged] = field(default_factory=list)

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        self.events.append(event)

    async def handle_message(self, message: IncomingMessage) -> None:
        _ = message


@dataclass
class FakeActorFactory:
    actor_type: str = "fake"
    started_actor_types: list[str] = field(default_factory=list)
    failing_actor_ids: set[str] = field(default_factory=set)

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        _ = mailbox
        self.started_actor_types.append(binding.actor.type)
        if binding.actor.id in self.failing_actor_ids:
            raise RuntimeError(f"boom: {binding.actor.id}")
        return FakeActor(binding)


def _actor_manager(
    resources: Resources,
    registry: ActorFactoryRegistry,
    workspace_root: Path,
) -> ActorManager:
    return ActorManager(
        repository=resources.repository,
        factories=registry,
        gateway=Gateway(routes=RouteBindings(rules=())),
        workspace_resolver=ActorWorkspaceResolver(workspace_root / "workspaces"),
    )


async def _insert_actor_bundle(
    resources: Resources,
    actor_id: str,
    *,
    actor_type: str,
) -> ActorRecord:
    character = await resources.repository.insert(
        CharacterORM,
        CharacterRecord(
            id=f"{actor_id}-char",
            name=f"{actor_id}-char",
            description="",
            system_prompt="You are test",
            facade_module="yuubot.core.facade",
            default_hints=CharacterHints(),
        ),
    )
    backend = await resources.repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id=f"{actor_id}-backend",
            name=f"{actor_id}-backend",
            yuuagents_provider="openai",
            default_model="gpt-4",
            model_capabilities=ModelCapabilities(),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )
    return await resources.repository.insert(
        ActorORM,
        ActorRecord(
            id=actor_id,
            name=actor_id,
            type=actor_type,
            character=character,
            llm_backend=backend,
            budget=YuuAgentBudget(),
            llm_options=YuuAgentLLMOptions(),
            model="",
            agent_tools=(),
            allowed_capability_ids=(),
            runtime_policy=RuntimePolicy(),
            resource_policy=ResourcePolicy(),
        ),
    )
