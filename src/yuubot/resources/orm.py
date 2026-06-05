from __future__ import annotations

from collections.abc import Iterable
from inspect import isawaitable
from typing import Any, TypeVar, cast

import msgspec
from tortoise import Model

from yuubot.core.secrets import SecretCodec, decrypt_secret_values, encrypt_secret_values
from yuubot.resources.store.model_factory import ReferenceSpec
from yuubot.resources.store.protocol import (
    generated_fields_of,
    references_of,
    schema_fields_of,
    schema_type_of,
)

ResourceT = TypeVar("ResourceT")


async def from_orm(
    row: Model,
    resource_type: type[ResourceT],
    *,
    secret_codec: SecretCodec | None = None,
) -> ResourceT:
    fields = dict(cast(Iterable[tuple[str, object]], row))
    for name, reference in _references(type(row)).items():
        fields.pop(f"{name}_id", None)
        fields[name] = await _referenced_resource(row, name, reference, secret_codec)
    if secret_codec is not None:
        fields = cast(dict[str, object], decrypt_secret_values(fields, secret_codec))
    return msgspec.convert(fields, type=resource_type, strict=False)


def to_orm_fields(
    resource: object,
    row_type: type[Model],
    *,
    secret_codec: SecretCodec | None = None,
) -> dict[str, Any]:
    try:
        schema_type = schema_type_of(row_type)
    except AttributeError:
        raise TypeError(f"{row_type.__name__} is not derived from a resource schema") from None
    if not isinstance(resource, schema_type):
        raise TypeError(
            f"{row_type.__name__} expects {schema_type.__name__}, "
            f"got {type(resource).__name__}"
        )

    if secret_codec is None:
        values = msgspec.to_builtins(resource)
    else:
        values = msgspec.to_builtins(
            resource,
            enc_hook=lambda value: encrypt_secret_values(value, secret_codec),
        )
    if not isinstance(values, dict):
        raise TypeError(f"{type(resource).__name__} did not convert to a field dict")

    expected_fields = schema_fields_of(row_type)
    actual_fields = set(values)
    if actual_fields != expected_fields:
        missing = ", ".join(sorted(expected_fields - actual_fields))
        extra = ", ".join(sorted(actual_fields - expected_fields))
        detail = ", ".join(part for part in (f"missing: {missing}", f"extra: {extra}") if part)
        raise ValueError(f"{row_type.__name__} fields are not schema-aligned ({detail})")

    generated_fields = generated_fields_of(row_type)
    orm_fields = {
        name: value
        for name, value in values.items()
        if name not in generated_fields
    }
    return _replace_references_with_ids(orm_fields, row_type)


def to_orm_update_fields(
    row_type: type[Model],
    fields: dict[str, Any],
    *,
    secret_codec: SecretCodec | None = None,
) -> dict[str, Any]:
    prepared = encrypt_secret_values(fields, secret_codec) if secret_codec else fields
    return _replace_references_with_ids(dict(cast(dict[str, Any], prepared)), row_type)


def referenced_field_names(row_type: type[Model]) -> tuple[str, ...]:
    return tuple(_references(row_type))


async def _referenced_resource(
    row: Model,
    name: str,
    reference: object,
    secret_codec: SecretCodec | None,
) -> msgspec.Struct | None:
    if getattr(row, f"{name}_id", None) is None:
        return None
    related = getattr(row, name)
    if isinstance(related, Model):
        related_row = related
    elif isawaitable(related):
        related_row = await related
    else:
        raise TypeError(f"{type(row).__name__}.{name} is not a model relation")
    schema_type = schema_type_of(type(related_row))
    return await from_orm(related_row, schema_type, secret_codec=secret_codec)


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


def _reference_id(value: object, reference: ReferenceSpec) -> str | None:
    if value is None:
        return None
    source_field = reference.source_field
    if isinstance(value, dict):
        value_dict = cast(dict[str, object], value)
        try:
            raw = value_dict[source_field]
            return str(raw) if raw is not None else None
        except KeyError as exc:
            raise ValueError(f"referenced object is missing {source_field!r}") from exc
    row_id = getattr(value, source_field, None)
    if row_id is None:
        raise ValueError(f"{type(value).__name__} is missing {source_field!r}")
    return str(row_id)


def _references(row_type: type[Model]) -> dict[str, ReferenceSpec]:
    return cast(dict[str, ReferenceSpec], references_of(row_type))
