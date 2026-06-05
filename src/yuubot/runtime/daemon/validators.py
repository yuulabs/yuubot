"""Validation for resource command endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def _extract_id(value: Any, field: str = "id") -> str | None:
    """Extract an ID from a value that may be a dict or a typed record.

    At the HTTP boundary, FK references arrive as dicts (e.g. ``{"id": "abc"}``).
    After normalization they become typed records with an ``.id`` attribute.
    This helper unifies both paths without ``isinstance`` branching at call sites.
    """
    if isinstance(value, dict):
        raw = value.get(field)
        return str(raw) if raw is not None else None
    return getattr(value, field, None)


async def validate_actor_references(
    fields: dict[str, Any],
    repository: ResourceRepository,
) -> None:
    """Check that actor FK references exist."""
    character = fields.get("character")
    if character is not None:
        char_id = _extract_id(character)
        if char_id and not await repository.get(CharacterORM, char_id):
            raise ValidationError("validation_error", f"character '{char_id}' not found")

    llm_backend = fields.get("llm_backend")
    if llm_backend is not None:
        backend_id = _extract_id(llm_backend)
        if backend_id and not await repository.get(LLMBackendORM, backend_id):
            raise ValidationError("validation_error", f"llm_backend '{backend_id}' not found")


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
