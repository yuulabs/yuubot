"""Actor workspace allocation and Python cwd behavior."""

from __future__ import annotations

from pathlib import Path

from yuubot.core.actors import ActorPythonSessionFactory, ActorWorkspaceResolver
from yuubot.core.bindings import load_actor_binding
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
from yuubot.core.routing import RouteBindings
from yuubot.resources.records import (
    ActorRecord,
    BudgetPolicy,
    CapabilitySetRecord,
    CharacterHints,
    CharacterRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import (
    ActorORM,
    CapabilitySetORM,
    CharacterORM,
    LLMBackendORM,
)


async def test_actor_workspace_resolver_keeps_special_ids_under_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    resolver = ActorWorkspaceResolver(root)

    workspace = resolver.resolve("../main actor/../../target")

    assert workspace.is_relative_to(root.resolve() / "actors")
    assert workspace.exists()
    assert ".." not in workspace.name


async def test_actor_python_session_uses_actor_workspace_cwd(
    resources: Resources,
    tmp_path: Path,
) -> None:
    repository = resources.repository
    actor = await _create_actor_bundle(repository, "python actor")
    resolver = ActorWorkspaceResolver(tmp_path / "workspace")
    workspace = resolver.resolve(actor.id)
    actor_binding = await load_actor_binding(repository, actor.id)
    binding = actor_binding.default_agent_binding(workspace_path=workspace)
    integrations = IntegrationCore(
        repository=repository,
        factories=IntegrationFactoryRegistry(),
        gateway=Gateway(routes=RouteBindings(rules=())),
        integrations_root=tmp_path / "data" / "integrations",
    )
    python_sessions = ActorPythonSessionFactory.in_directory(
        integrations=integrations,
        root=tmp_path / "facades",
    )

    session = await python_sessions.create(binding)
    try:
        output = await session.execute("import os\nprint(os.getcwd())")
        context_output = await session.execute("import yb\nprint(yb.actor.context())")
    finally:
        await session.close()
        await python_sessions.stop()

    assert str(workspace) in repr(output)
    assert "'actor_id': 'python actor'" in repr(context_output)
    assert "'agent_name': 'python actor'" in repr(context_output)


async def test_each_actor_gets_a_distinct_workspace(tmp_path: Path) -> None:
    resolver = ActorWorkspaceResolver(tmp_path / "workspace")

    first = resolver.resolve("actor/main")
    second = resolver.resolve("actor:main")

    assert first != second
    assert first.is_dir()
    assert second.is_dir()


async def _create_actor_bundle(
    repository: ResourceRepository,
    actor_id: str,
) -> ActorRecord:
    character = await repository.insert(
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
    backend = await repository.insert(
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
            default_character=character,
            capability_set=capability_set,
            default_llm_backend=backend,
            default_model="",
        ),
    )
