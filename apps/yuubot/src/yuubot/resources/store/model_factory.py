"""Derive Tortoise models from msgspec resource records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from types import NoneType, UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin

import msgspec
from tortoise import Model, fields

from yuubot.resources.store.protocol import schema_type_of

FieldKind = Literal["auto", "char", "text", "json", "datetime", "bool", "int", "float"]


@dataclass
class FieldSpec:
    kind: FieldKind = "auto"
    max_length: int = 255
    primary_key: bool = False
    unique: bool = False
    null: bool | None = None


@dataclass
class ReferenceSpec:
    """Explicit nested resource reference.

    Tortoise exposes the relation as ``field`` and stores the raw FK column as
    ``field_id``. The msgspec record keeps the nested object at ``field``.
    """

    row_type: type[Model]
    source_field: str = "id"
    related_name: str | None | Literal[False] = False
    on_delete: fields.OnDelete = fields.RESTRICT
    null: bool | None = None


def char(
    max_length: int = 255,
    *,
    primary_key: bool = False,
    unique: bool = False,
    null: bool | None = None,
) -> FieldSpec:
    return FieldSpec(
        kind="char",
        max_length=max_length,
        primary_key=primary_key,
        unique=unique,
        null=null,
    )


def text(*, null: bool | None = None) -> FieldSpec:
    return FieldSpec(kind="text", null=null)


def reference(
    row_type: type[Model],
    *,
    source_field: str = "id",
    related_name: str | None | Literal[False] = False,
    on_delete: fields.OnDelete = fields.RESTRICT,
    null: bool | None = None,
) -> ReferenceSpec:
    return ReferenceSpec(
        row_type=row_type,
        source_field=source_field,
        related_name=related_name,
        on_delete=on_delete,
        null=null,
    )


def resource_model(
    name: str,
    schema_type: type[msgspec.Struct],
    *,
    table: str,
    field_specs: Mapping[str, FieldSpec] | None = None,
    references: Mapping[str, ReferenceSpec] | None = None,
    unique_together: tuple[tuple[str, ...], ...] = (),
    module: str,
) -> type[Model]:
    specs = field_specs or {}
    refs = references or {}
    schema_fields = tuple(msgspec.structs.fields(schema_type))
    field_names = tuple(field.name for field in schema_fields)
    unknown_specs = set(specs).difference(field_names)
    if unknown_specs:
        names = ", ".join(sorted(unknown_specs))
        raise ValueError(f"{name} has field specs for unknown schema fields: {names}")
    unknown_refs = set(refs).difference(field_names)
    if unknown_refs:
        names = ", ".join(sorted(unknown_refs))
        raise ValueError(f"{name} has references for unknown schema fields: {names}")
    duplicate_specs = set(specs).intersection(refs)
    if duplicate_specs:
        names = ", ".join(sorted(duplicate_specs))
        raise ValueError(
            f"{name} cannot define field_specs and references for: {names}"
        )
    unknown_unique_fields = set(chain.from_iterable(unique_together)).difference(
        field_names
    )
    if unknown_unique_fields:
        names = ", ".join(sorted(unknown_unique_fields))
        raise ValueError(
            f"{name} has unique_together entries for unknown schema fields: {names}"
        )

    attrs: dict[str, Any] = {"__module__": module}
    generated_fields: list[str] = []
    for field in schema_fields:
        if field.name in refs:
            attrs[field.name] = _reference_field(field, refs[field.name])
        else:
            spec = specs.get(field.name, FieldSpec())
            attrs[field.name] = _tortoise_field(field, spec)
        if field.name in {"created_at", "updated_at"}:
            generated_fields.append(field.name)

    attrs["_yuubot_schema_type"] = schema_type
    attrs["_yuubot_schema_fields"] = frozenset(field_names)
    attrs["_yuubot_generated_fields"] = frozenset(generated_fields)
    attrs["_yuubot_references"] = dict(refs)

    def _to_builtins(self, *, recursive: bool = False) -> dict[str, object]:
        """Serialize model instance to a plain dict for msgspec.convert.

        Design trade-off — our models are created via ``type()`` at module
        level, so no type checker can see their attributes.  We *could* use
        ``getattr()`` with silent defaults at every call site (hides both
        type errors and schema drift), or we centralise the dynamic access
        here in the factory and expose a typed boundary via
        ``protocol.to_builtins()``.

        When *recursive* is ``False`` (default), FK/reference fields are
        skipped — ``getattr(self, name)`` on a scalar column is a pure
        Python attribute read, never a DB round-trip.  Pass
        ``recursive=True`` when you accept that FK traversal may trigger
        lazy loads.
        """
        refs = self._yuubot_references
        result: dict[str, object] = {}
        for name in self._yuubot_schema_fields:
            if not recursive and name in refs:
                continue
            value = getattr(self, name)
            if recursive and name in refs and value is not None:
                value = value._to_builtins(recursive=True)
            result[name] = value
        return result

    attrs["_to_builtins"] = _to_builtins

    attrs["Meta"] = type(
        "Meta",
        (),
        {
            "table": table,
            **({"unique_together": unique_together} if unique_together else {}),
        },
    )
    return type(name, (Model,), attrs)


def _reference_field(
    field: msgspec.structs.FieldInfo,
    spec: ReferenceSpec,
) -> fields.Field[Any]:
    field_type, nullable = _unwrap_optional(field.type)
    try:
        referenced_schema = schema_type_of(spec.row_type)
    except AttributeError:
        raise TypeError(f"{spec.row_type.__name__} is not a resource model") from None
    if field_type is not referenced_schema:
        raise TypeError(
            f"{field.name} must be {referenced_schema.__name__} "
            f"to reference {spec.row_type.__name__}"
        )
    null = nullable if spec.null is None else spec.null
    if null:
        return cast(
            fields.Field[Any],
            fields.ForeignKeyField(
                f"models.{spec.row_type.__name__}",
                related_name=spec.related_name,
                on_delete=spec.on_delete,
                null=True,
            ),
        )
    return cast(
        fields.Field[Any],
        fields.ForeignKeyField(
            f"models.{spec.row_type.__name__}",
            related_name=spec.related_name,
            on_delete=spec.on_delete,
            null=False,
        ),
    )


def _tortoise_field(
    field: msgspec.structs.FieldInfo,
    spec: FieldSpec,
) -> fields.Field[Any]:
    if field.name == "created_at":
        return fields.DatetimeField(auto_now_add=True)
    if field.name == "updated_at":
        return fields.DatetimeField(auto_now=True)

    field_type, nullable = _unwrap_optional(field.type)
    null = nullable if spec.null is None else spec.null
    kind = _field_kind(field_type, spec.kind)
    kwargs = _common_kwargs(field, spec, kind, null)

    if kind == "char":
        return fields.CharField(max_length=spec.max_length, **kwargs)
    if kind == "text":
        return fields.TextField(**kwargs)
    if kind == "datetime":
        return fields.DatetimeField(**kwargs)
    if kind == "bool":
        return fields.BooleanField(**kwargs)
    if kind == "int":
        return fields.IntField(**kwargs)
    if kind == "float":
        return fields.FloatField(**kwargs)
    if kind == "json":
        return fields.JSONField(**kwargs)
    raise TypeError(f"unsupported field kind {kind!r} for {field.name}")


def _field_kind(field_type: object, requested: FieldKind) -> FieldKind:
    if requested != "auto":
        return requested
    if field_type is str:
        return "char"
    if field_type is datetime:
        return "datetime"
    if field_type is bool:
        return "bool"
    if field_type is int:
        return "int"
    if field_type is float:
        return "float"
    return "json"


def _common_kwargs(
    field: msgspec.structs.FieldInfo,
    spec: FieldSpec,
    kind: FieldKind,
    null: bool,
) -> dict[str, object]:
    kwargs: dict[str, object] = {"null": null}
    if spec.primary_key:
        kwargs["primary_key"] = True
    if spec.unique:
        kwargs["unique"] = True
    default = _simple_default(field, kind, primary_key=spec.primary_key)
    if default is not msgspec.NODEFAULT:
        kwargs["default"] = default
    return kwargs


def _simple_default(
    field: msgspec.structs.FieldInfo,
    kind: FieldKind,
    *,
    primary_key: bool,
) -> object:
    if primary_key or kind == "json" or field.default is msgspec.NODEFAULT:
        return msgspec.NODEFAULT
    if field.default is None:
        return msgspec.NODEFAULT
    if isinstance(field.default, str | bool | int | float):
        return field.default
    return msgspec.NODEFAULT


def _unwrap_optional(field_type: object) -> tuple[object, bool]:
    origin = get_origin(field_type)
    if origin not in {Union, UnionType}:
        return field_type, False
    args = get_args(field_type)
    if NoneType not in args:
        return field_type, False
    non_none = tuple(arg for arg in args if arg is not NoneType)
    if len(non_none) != 1:
        return field_type, True
    return non_none[0], True
