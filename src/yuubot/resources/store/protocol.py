"""Typed accessors for ORM model metadata stamped by resource_model().

These attributes are dynamically stamped by ``resource_model()`` onto
Tortoise ORM classes at class-creation time.  The type checker cannot see
them, so we centralise all ``getattr`` access here — the single module
where it is acceptable — and expose typed functions for the rest of the
codebase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast

import msgspec
from tortoise import Model

if TYPE_CHECKING:
    from yuubot.resources.store.model_factory import ReferenceSpec

RecordT = TypeVar("RecordT", bound=msgspec.Struct)


def schema_type_of(orm_type: type[Model]) -> type[msgspec.Struct]:
    """Return the msgspec Struct type that this ORM model was derived from."""
    return cast(type[msgspec.Struct], getattr(orm_type, "_yuubot_schema_type"))


def schema_fields_of(orm_type: type[Model]) -> frozenset[str]:
    """Return the set of schema field names for this ORM model."""
    return cast(frozenset[str], getattr(orm_type, "_yuubot_schema_fields"))


def generated_fields_of(orm_type: type[Model]) -> frozenset[str]:
    """Return the set of auto-generated field names (created_at, updated_at)."""
    return cast(frozenset[str], getattr(orm_type, "_yuubot_generated_fields"))


def references_of(orm_type: type[Model]) -> dict[str, ReferenceSpec]:
    """Return the reference specs for this ORM model."""
    return cast(
        "dict[str, ReferenceSpec]",
        getattr(orm_type, "_yuubot_references", {}),
    )


def to_builtins(row: Model, *, recursive: bool = False) -> dict[str, object]:
    """Serialize a model instance to a plain dict for msgspec.convert.

    Design trade-off — ``_to_builtins`` is dynamically set on each ORM
    class at creation time (see ``resource_model()``); the type checker
    cannot see it.  This wrapper is the single centralised ``getattr``
    call — the rest of the codebase passes through here and stays typed.

    When *recursive* is ``False`` (default), FK/reference fields are
    skipped to avoid lazy database calls.  Pass ``recursive=True`` to
    include nested model data.
    """
    return cast(
        "dict[str, object]",
        getattr(row, "_to_builtins")(recursive=recursive),
    )