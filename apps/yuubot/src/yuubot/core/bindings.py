"""Actor binding queries from persisted schemas."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import msgspec

from yuubot.core.llm import BoundLLM
from yuubot.core.validation import GenerationParams, validate_generation_params
from yuubot.resources.records import (
    ActorRecord,
    CapabilitySetRecord,
    ConversationRecord,
    LLMBackendRecord,
    ResolvedActor,
    YuuAgentBudget,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import (
    ActorORM,
    CapabilitySetORM,
    ConversationORM,
    LLMBackendORM,
)


class ActorBinding(msgspec.Struct):
    """Always-on actor identity and defaults."""

    resolved: ResolvedActor
    workspace_path: Path | None = None

    @property
    def actor(self) -> ActorRecord:
        return self.resolved.actor

    @property
    def actor_id(self) -> str:
        return self.actor.id

    @property
    def actor_name(self) -> str:
        return self.actor.name

    @property
    def actor_type(self) -> str:
        return self.actor.type

    def default_agent_binding(
        self, *, workspace_path: Path | None = None
    ) -> "AgentBinding":
        return AgentBinding(
            owner_id=self.actor.id,
            agent_name=self.actor.name,
            actor=self.actor,
            capability_set=self.resolved.capability_set,
            llm=_bound_llm(
                self.actor.name,
                self.actor.generation_override,
                self.resolved.llm_backend,
                self.actor.model,
            ),
            budget=self.actor.per_run_budget,
            workspace_path=workspace_path or self.workspace_path,
        )


class AgentBinding(msgspec.Struct):
    """Complete bundle needed to materialize one yuuagents Agent."""

    owner_id: str
    agent_name: str
    actor: ActorRecord
    capability_set: CapabilitySetRecord
    llm: BoundLLM
    budget: YuuAgentBudget
    workspace_path: Path | None = None

    def require_workspace_path(self) -> Path:
        if self.workspace_path is None:
            raise RuntimeError(f"agent {self.agent_name!r} has no workspace path")
        return self.workspace_path


async def load_actor_binding(
    repository: ResourceRepository,
    actor_id: str,
    *,
    workspace_path: Path | None = None,
) -> ActorBinding:
    resolved = await resolve_actor(repository, actor_id)
    return ActorBinding(
        resolved=resolved,
        workspace_path=workspace_path,
    )


async def resolve_actor(
    repository: ResourceRepository,
    actor_id: str,
) -> ResolvedActor:
    actor = await _active_actor(repository, actor_id)
    capability_set = await _require_capability_set(repository, actor.capability_set_id)
    llm_backend = await _require_llm_backend(repository, actor.llm_backend_id)
    return ResolvedActor(
        actor=actor,
        capability_set=capability_set,
        llm_backend=llm_backend,
    )


async def load_conversation_agent_binding(
    repository: ResourceRepository,
    conversation_id: str,
    *,
    workspace_path: Path | None = None,
) -> AgentBinding:
    conversation = await repository.get(ConversationORM, conversation_id)
    if conversation is None:
        raise LookupError(f"conversation {conversation_id!r} does not exist")
    return await conversation_agent_binding(
        repository,
        conversation,
        workspace_path=workspace_path,
    )


async def conversation_agent_binding(
    repository: ResourceRepository,
    conversation: ConversationRecord,
    *,
    workspace_path: Path | None = None,
) -> AgentBinding:
    resolved = await resolve_actor(repository, conversation.actor_id)
    return AgentBinding(
        owner_id=conversation.conversation_id,
        agent_name=f"conversation:{conversation.conversation_id}",
        actor=resolved.actor,
        capability_set=resolved.capability_set,
        llm=_bound_llm(
            conversation.conversation_id,
            resolved.actor.generation_override,
            resolved.llm_backend,
            resolved.actor.model,
        ),
        budget=resolved.actor.per_run_budget,
        workspace_path=workspace_path,
    )


async def _active_actor(
    repository: ResourceRepository,
    actor_id: str,
) -> ActorRecord:
    actor = await repository.get(ActorORM, actor_id)
    if actor is None or not actor.enabled:
        raise KeyError(f"active actor {actor_id} does not exist")
    return actor


async def _require_capability_set(
    repository: ResourceRepository,
    capability_set_id: str,
) -> CapabilitySetRecord:
    capability_set = await repository.get(CapabilitySetORM, capability_set_id)
    if capability_set is None:
        raise KeyError(f"capability set {capability_set_id!r} does not exist")
    return capability_set


async def _require_llm_backend(
    repository: ResourceRepository,
    llm_backend_id: str,
) -> LLMBackendRecord:
    llm_backend = await repository.get(LLMBackendORM, llm_backend_id)
    if llm_backend is None:
        raise KeyError(f"llm backend {llm_backend_id!r} does not exist")
    return llm_backend


def _bound_llm(
    context_name: str,
    generation_override: GenerationParams,
    backend: LLMBackendRecord,
    model: str,
) -> BoundLLM:
    generation_params = _merge_generation_params(
        backend.default_generation_params,
        generation_override,
    )
    validate_generation_params(
        msgspec.to_builtins(generation_params),
        context=f"agent[{context_name}].generation_params",
    )
    return BoundLLM(
        backend=backend,
        model=model or backend.recommended_model,
        generation_params=generation_params,
    )


def _merge_generation_params(
    base: GenerationParams,
    override: GenerationParams,
) -> GenerationParams:
    return GenerationParams(
        max_tokens=_coalesce(override.max_tokens, base.max_tokens),
        temperature=_coalesce(override.temperature, base.temperature),
        top_p=_coalesce(override.top_p, base.top_p),
        stop=_coalesce(override.stop, base.stop),
    )


ValueT = TypeVar("ValueT")


def _coalesce(first: ValueT | None, second: ValueT | None) -> ValueT | None:
    return first if first is not None else second
