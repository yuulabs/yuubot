"""Validation for resource command endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from yuubot.resources.orm import from_orm
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.records import ConversationRecord
from yuubot.resources.store.models import (
    ActorORM,
    CapabilitySetORM,
    CharacterORM,
    ConversationORM,
    LLMBackendORM,
)


@dataclass
class ValidationError(Exception):
    code: str
    detail: str


async def validate_delete_not_referenced(
    orm_type: type,
    row_id: str,
    repository: ResourceRepository,
) -> None:
    """Prevent deletion of resources that are referenced by actors."""
    if orm_type is CharacterORM:
        actors = await repository.list(ActorORM)
        referencing = [
            a
            for a in actors
            if a.default_character is not None and a.default_character.id == row_id
        ]
        conversations = await _conversation_rows(repository)
        referencing.extend(
            c for c in conversations if c.character is not None and c.character.id == row_id
        )
        if referencing:
            raise ValidationError(
                "conflict",
                f"character is referenced by {len(referencing)} resource(s)",
            )
    elif orm_type is CapabilitySetORM:
        actors = await repository.list(ActorORM)
        referencing = [
            a
            for a in actors
            if a.capability_set is not None and a.capability_set.id == row_id
        ]
        conversations = await _conversation_rows(repository)
        referencing.extend(
            c
            for c in conversations
            if c.capability_set is not None and c.capability_set.id == row_id
        )
        if referencing:
            raise ValidationError(
                "conflict",
                f"capability_set is referenced by {len(referencing)} resource(s)",
            )
    elif orm_type is LLMBackendORM:
        actors = await repository.list(ActorORM)
        referencing = [
            a
            for a in actors
            if a.default_llm_backend is not None and a.default_llm_backend.id == row_id
        ]
        conversations = await _conversation_rows(repository)
        referencing.extend(
            c
            for c in conversations
            if c.llm_backend is not None and c.llm_backend.id == row_id
        )
        if referencing:
            raise ValidationError(
                "conflict",
                f"llm_backend is referenced by {len(referencing)} resource(s)",
            )


async def _conversation_rows(repository: ResourceRepository) -> list[ConversationRecord]:
    with repository.store.db.activate():
        rows = await ConversationORM.all().select_related(
            "character",
            "capability_set",
            "llm_backend",
        )
        return [
            await from_orm(row, ConversationRecord, secret_codec=repository.secret_codec)
            for row in rows
        ]
