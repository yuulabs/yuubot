"""Actor binding queries from persisted schemas."""

from __future__ import annotations

from pathlib import Path

import msgspec

from yuubot.core.llm import BoundLLM
from yuubot.core.validation import validate_stream_options
from yuubot.resources.records import (
    ActorRecord,
    CharacterRecord,
    LLMBackendRecord,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import ActorORM


class ActorBinding(msgspec.Struct):
    """Complete resource bundle needed to assemble yuuagents objects."""

    actor: ActorRecord
    character: CharacterRecord
    llm: BoundLLM
    workspace_path: Path | None = None

    def require_workspace_path(self) -> Path:
        if self.workspace_path is None:
            raise RuntimeError(f"actor {self.actor.id!r} has no workspace path")
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
        character=actor.character,
        llm=_bound_llm(actor, actor.llm_backend),
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


def _bound_llm(actor: ActorRecord, backend: LLMBackendRecord) -> BoundLLM:
    merged = {
        **msgspec.to_builtins(backend.default_stream_options),
        **msgspec.to_builtins(actor.llm_options.stream_options),
    }
    validate_stream_options(
        merged,
        context=f"actor[{actor.name}].stream_options",
    )
    return BoundLLM(
        backend=backend,
        model=actor.model or backend.default_model,
        stream_options=merged,
    )
