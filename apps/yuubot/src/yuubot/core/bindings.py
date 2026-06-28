"""Actor binding queries from persisted schemas."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import msgspec

from yuubot.core.llm import BoundLLM
from yuubot.core.skills import SkillInfo, loaded_skills
from yuubot.core.validation import (
    ConfigurationError,
    GenerationParams,
    validate_generation_params,
)
from yuubot.resources.orm import from_orm
from yuubot.resources.records import (
    ActorRecord,
    CapabilitySetRecord,
    ConversationHistoryItemRecord,
    ConversationRecord,
    LLMBackendRecord,
    ResolvedActor,
    ResolvedConversation,
    RunBudget,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import (
    ActorORM,
    CapabilitySetORM,
    ConversationHistoryItemORM,
    ConversationORM,
    LLMBackendORM,
)


class ActorBinding(msgspec.Struct):
    """Always-on actor identity and defaults."""

    resolved: ResolvedActor
    workspace_path: Path | None = None
    global_skills_path: Path | None = None

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
            skills=loaded_skills(
                global_root=self.global_skills_path or Path(""),
                actor_workspace=workspace_path or self.workspace_path,
                scope=self.actor.skill_scope,
                include_content=False,
            ),
        )


class AgentBinding(msgspec.Struct):
    """Complete bundle needed to materialize one yuuagents Agent."""

    owner_id: str
    agent_name: str
    actor: ActorRecord
    capability_set: CapabilitySetRecord
    llm: BoundLLM
    budget: RunBudget
    workspace_path: Path | None = None
    skills: tuple[SkillInfo, ...] = ()

    def require_workspace_path(self) -> Path:
        if self.workspace_path is None:
            raise RuntimeError(f"agent {self.agent_name!r} has no workspace path")
        return self.workspace_path


async def load_actor_binding(
    repository: ResourceRepository,
    actor_id: str,
    *,
    workspace_path: Path | None = None,
    global_skills_path: Path | None = None,
) -> ActorBinding:
    resolved = await resolve_actor(repository, actor_id)
    return ActorBinding(
        resolved=resolved,
        workspace_path=workspace_path,
        global_skills_path=global_skills_path,
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
    resolved = await resolve_conversation(repository, conversation_id)
    return agent_binding_from_resolved_conversation(
        resolved,
        workspace_path=workspace_path,
    )


async def conversation_agent_binding(
    repository: ResourceRepository,
    conversation: ConversationRecord,
    *,
    workspace_path: Path | None = None,
) -> AgentBinding:
    resolved = await resolve_conversation_record(repository, conversation)
    return agent_binding_from_resolved_conversation(
        resolved,
        workspace_path=workspace_path,
    )


async def resolve_conversation(
    repository: ResourceRepository,
    conversation_id: str,
) -> ResolvedConversation:
    with repository.store.db.activate():
        row = await ConversationORM.get_or_none(conversation_id=conversation_id)
    conversation = None
    if row is not None:
        conversation = await from_orm(
            row,
            ConversationRecord,
            secret_codec=repository.secret_codec,
        )
    if conversation is None:
        raise LookupError(f"conversation {conversation_id!r} does not exist")
    return await resolve_conversation_record(repository, conversation)


async def resolve_conversation_record(
    repository: ResourceRepository,
    conversation: ConversationRecord,
) -> ResolvedConversation:
    resolved_actor = await resolve_actor(repository, conversation.actor_id)
    history = await _load_history_items(repository, conversation.conversation_id)
    return ResolvedConversation(
        conversation=conversation,
        actor=resolved_actor.actor,
        capability_set=resolved_actor.capability_set,
        llm_backend=resolved_actor.llm_backend,
        history=history,
    )


def agent_binding_from_resolved_conversation(
    resolved: ResolvedConversation,
    *,
    workspace_path: Path | None = None,
    global_skills_path: Path | None = None,
) -> AgentBinding:
    conversation = resolved.conversation
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
        skills=loaded_skills(
            global_root=global_skills_path or Path(""),
            actor_workspace=workspace_path,
            scope=resolved.actor.skill_scope,
            include_content=False,
        ),
    )


async def _load_history_items(
    repository: ResourceRepository,
    conversation_id: str,
) -> tuple[ConversationHistoryItemRecord, ...]:
    with repository.store.db.activate():
        rows = await ConversationHistoryItemORM.filter(
            conversation_id=conversation_id,
        ).order_by("id")
    records: list[ConversationHistoryItemRecord] = []
    for row in rows:
        records.append(
            await from_orm(
                row,
                ConversationHistoryItemRecord,
                secret_codec=repository.secret_codec,
            )
        )
    return tuple(records)


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
    if not model:
        raise ConfigurationError(f"agent {context_name!r}: actor model must be set")
    if model not in backend.model_configs:
        raise ConfigurationError(
            f"agent {context_name!r}: model {model!r} is not configured "
            f"in backend {backend.name!r}"
        )
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
        model=model,
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
