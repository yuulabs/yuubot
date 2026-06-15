"""Utility functions for request handling: encoding, response helpers, and
convenience-field resolution for actor create/patch requests.
"""

from __future__ import annotations

import uuid
from typing import Any

import msgspec
from starlette.responses import JSONResponse
from tortoise import Model

from yuubot.core.secrets import redact_secret_for_json
from yuubot.resources.records import (
    ResourcePolicy,
    RuntimePolicy,
    YuuAgentBudget,
)
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    CharacterORM,
    LLMBackendORM,
    PromptTemplateORM,
)
from yuubot.runtime.daemon.commands._schemas import (
    ActorCreateRequest,
    ActorIngressRulePatchRequest,
    CharacterPatchRequest,
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
        return msgspec.convert(raw, type=schema_type, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        return _error("validation_error", str(exc), 400)


def _ensure_record_id(record: msgspec.Struct) -> None:
    row_id = getattr(record, "id", "")
    if not row_id:
        setattr(record, "id", str(uuid.uuid4()))


def _struct_fields(record: msgspec.Struct) -> dict[str, Any]:
    """Convert a request Struct to a dict for further processing.

    ``msgspec.to_builtins`` is the legitimate serialisation boundary
    here — request Structs are converted to plain dicts for ORM update
    kwargs or nested field processing.  This is NOT a roundtrip: data
    flows Struct → dict one way only.
    """
    raw_fields = msgspec.to_builtins(record)
    if not isinstance(raw_fields, dict):
        raise TypeError(f"{type(record).__name__} did not encode to a field object")

    fields: dict[str, Any] = {}
    for name, value in raw_fields.items():
        if not isinstance(name, str):
            raise TypeError(f"{type(record).__name__} encoded a non-string field name")
        fields[name] = value
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
    CharacterORM: CharacterPatchRequest,
    PromptTemplateORM: PromptTemplatePatchRequest,
    ActorIngressRuleORM: ActorIngressRulePatchRequest,
}


def _patch_request_type(orm_type: type[Model]) -> type[msgspec.Struct] | None:
    return _PATCH_TYPES.get(orm_type)


# -- Actor create/patch convenience-field resolution --


def _actor_budget(request: ActorCreateRequest) -> YuuAgentBudget:
    if request.budget is not msgspec.UNSET:
        return request.budget
    if request.max_steps is not msgspec.UNSET:
        return YuuAgentBudget(max_steps=request.max_steps)
    return YuuAgentBudget()


def _actor_capability_ids(request: ActorCreateRequest) -> tuple[str, ...]:
    if request.allowed_capability_ids is not msgspec.UNSET:
        return request.allowed_capability_ids
    if request.capability_ids is not msgspec.UNSET:
        return request.capability_ids
    return ()


def _actor_runtime_policy(request: ActorCreateRequest) -> RuntimePolicy:
    if request.runtime_policy is not msgspec.UNSET:
        return request.runtime_policy
    if request.memory_enabled is not msgspec.UNSET:
        return RuntimePolicy(memory_enabled=request.memory_enabled)
    return RuntimePolicy()


def _actor_resource_policy(request: ActorCreateRequest) -> ResourcePolicy:
    if request.resource_policy is not msgspec.UNSET:
        return request.resource_policy
    if request.workspace_access is msgspec.UNSET and request.daily_budget is msgspec.UNSET:
        return ResourcePolicy()
    return ResourcePolicy(
        workspace_access=(
            request.workspace_access
            if request.workspace_access is not msgspec.UNSET
            else "none"
        ),
        budget_usd_daily=(
            request.daily_budget if request.daily_budget is not msgspec.UNSET else None
        ),
    )
