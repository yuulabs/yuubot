"""Utility functions for request handling: encoding and response helpers."""

from __future__ import annotations

import uuid
from typing import Any

import msgspec
from starlette.responses import JSONResponse
from tortoise import Model

from yuubot.core.secrets import redact_secret_for_json, secret_decode_hook
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    CapabilitySetORM,
    LLMBackendORM,
    PromptTemplateORM,
)
from yuubot.runtime.daemon.commands._schemas import (
    ActorIngressRulePatchRequest,
    CapabilitySetPatchRequest,
    LLMBackendPatchRequest,
    PromptTemplatePatchRequest,
    StructT,
    ValueT,
)


def _encode_record(record: object) -> object:
    return msgspec.json.decode(
        msgspec.json.encode(record, enc_hook=redact_secret_for_json)
    )


def _ok(
    data: object, actions: list[str] | None = None, status_code: int = 200
) -> JSONResponse:
    return JSONResponse(
        {"status": "ok", "data": _encode_record(data), "actions": actions or []},
        status_code=status_code,
    )


def _partial(data: object, warnings: list[str]) -> JSONResponse:
    return JSONResponse(
        {"status": "partial", "data": _encode_record(data), "warnings": warnings},
    )


def _error(code: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "code": code, "detail": detail},
        status_code=status_code,
    )


def _convert_request(raw: object, schema_type: type[StructT]) -> StructT | JSONResponse:
    try:
        return msgspec.convert(
            raw,
            type=schema_type,
            strict=False,
            dec_hook=secret_decode_hook,
        )
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        return _error("validation_error", str(exc), 400)


def _ensure_record_id(record: msgspec.Struct) -> None:
    row_id = getattr(record, "id", "")
    if not row_id:
        setattr(record, "id", str(uuid.uuid4()))


def _struct_fields(record: msgspec.Struct) -> dict[str, Any]:
    """Extract populated request Struct fields without serializing secrets."""

    fields: dict[str, Any] = {}
    for field in msgspec.structs.fields(record):
        value = getattr(record, field.name)
        if value is not msgspec.UNSET:
            fields[field.name] = value
    return fields


def _patch_fields(
    record: msgspec.Struct,
    *,
    exclude: frozenset[str] = frozenset({"id"}),
) -> dict[str, Any]:
    return {
        name: value
        for name, value in _struct_fields(record).items()
        if name not in exclude
    }


def _value_or(value: ValueT | msgspec.UnsetType, default: ValueT) -> ValueT:
    if value is msgspec.UNSET:
        return default
    return value


_PATCH_TYPES: dict[type[Model], type[msgspec.Struct]] = {
    LLMBackendORM: LLMBackendPatchRequest,
    CapabilitySetORM: CapabilitySetPatchRequest,
    PromptTemplateORM: PromptTemplatePatchRequest,
    ActorIngressRuleORM: ActorIngressRulePatchRequest,
}


def _patch_request_type(orm_type: type[Model]) -> type[msgspec.Struct] | None:
    return _PATCH_TYPES.get(orm_type)
