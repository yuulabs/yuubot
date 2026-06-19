"""Actor binding queries from persisted schemas."""

from __future__ import annotations

from pathlib import Path

import msgspec

from yuubot.core.llm import BoundLLM
from yuubot.core.validation import validate_stream_options
from yuubot.resources.records import (
    ActorRecord,
    CapabilitySetRecord,
    CharacterRecord,
    ConversationRecord,
    LLMBackendRecord,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import ActorORM, ConversationORM


class ActorBinding(msgspec.Struct):
    """Always-on actor identity and defaults."""

    actor: ActorRecord
    workspace_path: Path | None = None

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
            character=self.actor.default_character,
            capability_set=self.actor.capability_set,
            llm=_bound_llm(
                self.actor.name,
                self.actor.default_llm_options,
                self.actor.default_llm_backend,
                self.actor.default_model,
            ),
            llm_options=self.actor.default_llm_options,
            budget=self.actor.default_budget,
            workspace_path=workspace_path or self.workspace_path,
        )


class AgentBinding(msgspec.Struct):
    """Complete bundle needed to materialize one yuuagents Agent."""

    owner_id: str
    agent_name: str
    character: CharacterRecord
    capability_set: CapabilitySetRecord
    llm: BoundLLM
    llm_options: YuuAgentLLMOptions
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
    actor = await _active_actor(repository, actor_id)
    return ActorBinding(
        actor=actor,
        workspace_path=workspace_path,
    )


async def load_conversation_agent_binding(
    repository: ResourceRepository,
    conversation_id: str,
) -> AgentBinding:
    conversation = await repository.get(ConversationORM, conversation_id)
    if conversation is None:
        raise LookupError(f"conversation {conversation_id!r} does not exist")
    return conversation_agent_binding(conversation)


def conversation_agent_binding(
    conversation: ConversationRecord,
    *,
    workspace_path: Path | None = None,
) -> AgentBinding:
    return AgentBinding(
        owner_id=conversation.conversation_id,
        agent_name=f"conversation:{conversation.conversation_id}",
        character=conversation.character,
        capability_set=conversation.capability_set,
        llm=_bound_llm(
            conversation.conversation_id,
            conversation.llm_options,
            conversation.llm_backend,
            conversation.model,
        ),
        llm_options=conversation.llm_options,
        budget=conversation.budget,
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


def _bound_llm(
    context_name: str,
    llm_options,
    backend: LLMBackendRecord,
    model: str,
) -> BoundLLM:
    merged = {
        **msgspec.to_builtins(backend.default_stream_options),
        **msgspec.to_builtins(llm_options.stream_options),
    }
    validate_stream_options(
        merged,
        context=f"agent[{context_name}].stream_options",
    )
    return BoundLLM(
        backend=backend,
        model=model or backend.default_model,
        stream_options=merged,
    )
