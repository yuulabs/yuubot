"""Validation for resource command endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import (
    ActorORM,
    CharacterORM,
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
        referencing = [a for a in actors if a.character is not None and a.character.id == row_id]
        if referencing:
            raise ValidationError(
                "conflict",
                f"character is referenced by {len(referencing)} actor(s)",
            )
    elif orm_type is LLMBackendORM:
        actors = await repository.list(ActorORM)
        referencing = [a for a in actors if a.llm_backend is not None and a.llm_backend.id == row_id]
        if referencing:
            raise ValidationError(
                "conflict",
                f"llm_backend is referenced by {len(referencing)} actor(s)",
            )
