from __future__ import annotations

from collections.abc import Iterable
from inspect import isawaitable
from typing import Any, TypeVar, cast

import msgspec
from tortoise import Model

ResourceT = TypeVar("ResourceT")


async def from_orm(row: Model, resource_type: type[ResourceT]) -> ResourceT:
    fields = dict(cast(Iterable[tuple[str, object]], row))
    for name, reference in _references(type(row)).items():
        fields.pop(f"{name}_id", None)
        fields[name] = await _referenced_resource(row, name, reference)
    return msgspec.convert(fields, type=resource_type, strict=False)


def to_orm_fields(resource: object, row_type: type[Model]) -> dict[str, Any]:
    schema_type = getattr(row_type, "_yuubot_schema_type", None)
    if schema_type is None:
        raise TypeError(f"{row_type.__name__} is not derived from a resource schema")
    if not isinstance(resource, schema_type):
        raise TypeError(
            f"{row_type.__name__} expects {schema_type.__name__}, "
            f"got {type(resource).__name__}"
        )

    values = msgspec.to_builtins(resource)
    if not isinstance(values, dict):
        raise TypeError(f"{type(resource).__name__} did not convert to a field dict")

    expected_fields = cast(
        frozenset[str],
        getattr(row_type, "_yuubot_schema_fields"),
    )
    actual_fields = set(values)
    if actual_fields != expected_fields:
        missing = ", ".join(sorted(expected_fields - actual_fields))
        extra = ", ".join(sorted(actual_fields - expected_fields))
        detail = ", ".join(part for part in (f"missing: {missing}", f"extra: {extra}") if part)
        raise ValueError(f"{row_type.__name__} fields are not schema-aligned ({detail})")

    generated_fields = cast(
        frozenset[str],
        getattr(row_type, "_yuubot_generated_fields"),
    )
    orm_fields = {
        name: value
        for name, value in values.items()
        if name not in generated_fields
    }
    return _replace_references_with_ids(orm_fields, row_type)


def to_orm_update_fields(row_type: type[Model], fields: dict[str, Any]) -> dict[str, Any]:
    return _replace_references_with_ids(dict(fields), row_type)


def referenced_field_names(row_type: type[Model]) -> tuple[str, ...]:
    return tuple(_references(row_type))


async def _referenced_resource(row: Model, name: str, reference: object) -> object:
    if getattr(row, f"{name}_id", None) is None:
        return None
    related = getattr(row, name)
    if isinstance(related, Model):
        related_row = related
    elif isawaitable(related):
        related_row = await related
    else:
        raise TypeError(f"{type(row).__name__}.{name} is not a model relation")
    schema_type = getattr(type(related_row), "_yuubot_schema_type")
    return await from_orm(related_row, schema_type)


def _replace_references_with_ids(
    fields: dict[str, Any],
    row_type: type[Model],
) -> dict[str, Any]:
    for name, reference in _references(row_type).items():
        if name not in fields:
            continue
        value = fields.pop(name)
        fields[f"{name}_id"] = _reference_id(value, reference)
    return fields


def _reference_id(value: object, reference: object) -> object:
    if value is None:
        return None
    source_field = getattr(reference, "source_field")
    if isinstance(value, dict):
        try:
            return value[source_field]
        except KeyError as exc:
            raise ValueError(f"referenced object is missing {source_field!r}") from exc
    row_id = getattr(value, source_field, None)
    if row_id is None:
        raise ValueError(f"{type(value).__name__} is missing {source_field!r}")
    return row_id


def _references(row_type: type[Model]) -> dict[str, object]:
    return cast(dict[str, object], getattr(row_type, "_yuubot_references", {}))
