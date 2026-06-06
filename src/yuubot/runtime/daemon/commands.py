"""Resource command sub-application for the daemon.

HTTP handlers parse/validate input and format output.
All business logic (CRUD, reconcile, lifecycle) is delegated to ResourceService.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Literal, TypeVar

import msgspec
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from tortoise import Model

from yuubot.bootstrap.config import ServerConfig
from yuubot.resources.errors import StorageError
from yuubot.core.secrets import redact_secret_for_json, wrap_config_secrets
from yuubot.core.validation import LLMProviderOptions, StreamOptions
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    BudgetPolicy,
    CharacterHints,
    CharacterRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
    ResourcePolicy,
    RuntimePolicy,
    ToolConfig,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.registry import LifecycleHandler, ResourceTypeRegistry
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.service import ResourceService
from yuubot.resources.store.protocol import schema_type_of
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CharacterORM,
    IntegrationORM,
    LLMBackendORM,
    PromptTemplateORM,
)
from yuubot.runtime.daemon.validators import (
    ValidationError,
    validate_delete_not_referenced,
)

logger = logging.getLogger(__name__)

in_command_context: ContextVar[bool] = ContextVar("in_command_context", default=False)
StructT = TypeVar("StructT", bound=msgspec.Struct)
ValueT = TypeVar("ValueT")
WorkspaceAccess = Literal["none", "read_only", "read_write"]


# -- Request schemas: typed boundary for HTTP payloads --


class ActorCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    character: CharacterRecord | msgspec.UnsetType = msgspec.UNSET
    llm_backend: LLMBackendRecord | msgspec.UnsetType = msgspec.UNSET
    id: str = ""
    type: str = "simple_loop"
    character_id: str | msgspec.UnsetType = msgspec.UNSET
    llm_backend_id: str | msgspec.UnsetType = msgspec.UNSET
    model: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    llm_options: YuuAgentLLMOptions | msgspec.UnsetType = msgspec.UNSET
    budget: YuuAgentBudget | msgspec.UnsetType = msgspec.UNSET
    agent_tools: tuple[ToolConfig, ...] | msgspec.UnsetType = msgspec.UNSET
    allowed_capability_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    runtime_policy: RuntimePolicy | msgspec.UnsetType = msgspec.UNSET
    resource_policy: ResourcePolicy | msgspec.UnsetType = msgspec.UNSET
    max_steps: int | msgspec.UnsetType = msgspec.UNSET
    memory_enabled: bool | msgspec.UnsetType = msgspec.UNSET
    workspace_access: WorkspaceAccess | msgspec.UnsetType = msgspec.UNSET
    daily_budget: float | msgspec.UnsetType = msgspec.UNSET
    capability_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET


class ActorPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    type: str | msgspec.UnsetType = msgspec.UNSET
    character: CharacterRecord | msgspec.UnsetType = msgspec.UNSET
    llm_backend: LLMBackendRecord | msgspec.UnsetType = msgspec.UNSET
    character_id: str | msgspec.UnsetType = msgspec.UNSET
    llm_backend_id: str | msgspec.UnsetType = msgspec.UNSET
    model: str | msgspec.UnsetType = msgspec.UNSET
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    llm_options: YuuAgentLLMOptions | msgspec.UnsetType = msgspec.UNSET
    budget: YuuAgentBudget | msgspec.UnsetType = msgspec.UNSET
    agent_tools: tuple[ToolConfig, ...] | msgspec.UnsetType = msgspec.UNSET
    allowed_capability_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    runtime_policy: RuntimePolicy | msgspec.UnsetType = msgspec.UNSET
    resource_policy: ResourcePolicy | msgspec.UnsetType = msgspec.UNSET
    max_steps: int | msgspec.UnsetType = msgspec.UNSET
    memory_enabled: bool | msgspec.UnsetType = msgspec.UNSET
    workspace_access: WorkspaceAccess | msgspec.UnsetType = msgspec.UNSET
    daily_budget: float | msgspec.UnsetType = msgspec.UNSET
    capability_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET


class IntegrationCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    id: str = ""
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool = True
    version: int = 1
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET


class IntegrationPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class LLMBackendPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    yuuagents_provider: str | msgspec.UnsetType = msgspec.UNSET
    model_capabilities: ModelCapabilities | msgspec.UnsetType = msgspec.UNSET
    models: ModelCatalog | msgspec.UnsetType = msgspec.UNSET
    pricing: PricingTable | msgspec.UnsetType = msgspec.UNSET
    budget: BudgetPolicy | msgspec.UnsetType = msgspec.UNSET
    provider_options: LLMProviderOptions | msgspec.UnsetType = msgspec.UNSET
    default_model: str | msgspec.UnsetType = msgspec.UNSET
    default_stream_options: StreamOptions | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class CharacterPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    system_prompt: str | msgspec.UnsetType = msgspec.UNSET
    facade_module: str | msgspec.UnsetType = msgspec.UNSET
    default_hints: CharacterHints | msgspec.UnsetType = msgspec.UNSET
    is_builtin: bool | msgspec.UnsetType = msgspec.UNSET
    builtin_version: str | msgspec.UnsetType = msgspec.UNSET
    cloned_from: str | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class PromptTemplatePatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    content: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    is_builtin: bool | msgspec.UnsetType = msgspec.UNSET
    builtin_version: str | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class ActorIngressRulePatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    actor_id: str | msgspec.UnsetType = msgspec.UNSET
    source_id_pattern: str | msgspec.UnsetType = msgspec.UNSET
    source_path_pattern: str | msgspec.UnsetType = msgspec.UNSET
    kind_patterns: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class ActorIngressRuleCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    actor_id: str
    id: str = ""
    source_id_pattern: str = "*"
    source_path_pattern: str = "**"
    kind_patterns: tuple[str, ...] = ("*",)
    enabled: bool = True
    version: int = 1
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET


def build_default_resource_type_registry(
    *,
    integration_lifecycle_handler: LifecycleHandler | None = None,
    actor_lifecycle_handler: LifecycleHandler | None = None,
) -> ResourceTypeRegistry:
    """Create a ResourceTypeRegistry with all known resource types."""
    registry = ResourceTypeRegistry()
    registry.register("llm-backends", LLMBackendORM)
    registry.register(
        "integrations",
        IntegrationORM,
        lifecycle_realm="integrations",
        has_lifecycle=True,
        lifecycle_handler=integration_lifecycle_handler,
    )
    registry.register("characters", CharacterORM)
    registry.register(
        "actors",
        ActorORM,
        lifecycle_realm="actors",
        has_lifecycle=True,
        lifecycle_handler=actor_lifecycle_handler,
    )
    registry.register("ingress-rules", ActorIngressRuleORM)
    registry.register("prompt-templates", PromptTemplateORM)
    return registry


def _encode_record(record: object) -> object:
    return msgspec.json.decode(
        msgspec.json.encode(record, enc_hook=redact_secret_for_json)
    )


def _ok(data: object, actions: list[str] | None = None, status_code: int = 200) -> JSONResponse:
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


class SecretMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, secret: str):
        super().__init__(app)
        self.secret = secret

    async def dispatch(self, request: Request, call_next):
        if not self.secret:
            return _error("misconfigured", "daemon_secret not set", 500)
        if request.headers.get("x-daemon-secret") != self.secret:
            return _error("unauthorized", "X-Daemon-Secret missing or invalid", 403)
        return await call_next(request)


class ResourceCommandHandlers:
    """HTTP handlers for resource CRUD and lifecycle.

    Each handler parses/validates the request, delegates business logic
    to ResourceService, and formats the JSON response.
    """

    def __init__(
        self,
        service: ResourceService,
        type_registry: ResourceTypeRegistry,
        repository: ResourceRepository,
    ):
        self.service = service
        self.type_registry = type_registry
        self.repository = repository

    async def create(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        raw_payload = await self._read_json_body(request)
        if isinstance(raw_payload, JSONResponse):
            return raw_payload

        record = await self._decode_create_payload(orm_type, raw_payload)
        if isinstance(record, JSONResponse):
            return record

        token = in_command_context.set(True)
        try:
            inserted, actions, warnings = await self.service.create(orm_type, record)
        except StorageError as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if warnings:
            return _partial(inserted, warnings)
        return _ok(inserted, actions, status_code=201)

    async def get(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        record = await self.repository.get(orm_type, row_id)
        if record is None:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)
        return _ok(record)

    async def list_all(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        records = await self.repository.list(orm_type)
        return JSONResponse(
            {"status": "ok", "data": [_encode_record(r) for r in records]},
        )

    async def update(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        raw_payload = await self._read_json_body(request)
        if isinstance(raw_payload, JSONResponse):
            return raw_payload

        fields = await self._decode_update_payload(orm_type, row_id, raw_payload)
        if isinstance(fields, JSONResponse):
            return fields
        if not fields:
            return _error("validation_error", "no fields to update", 400)

        token = in_command_context.set(True)
        try:
            updated, actions, warnings = await self.service.update(
                orm_type, row_id, **fields,
            )
        except StorageError as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if updated is None:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)
        if warnings:
            return _partial(updated, warnings)
        return _ok(updated, actions)

    async def delete(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        try:
            await validate_delete_not_referenced(orm_type, row_id, self.repository)
        except ValidationError as exc:
            return _error(exc.code, exc.detail, 409)

        token = in_command_context.set(True)
        try:
            deleted, actions, warnings = await self.service.delete(orm_type, row_id)
        except StorageError as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if not deleted:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)
        return JSONResponse(
            {"status": "ok", "actions": list(actions), "warnings": warnings},
        )

    async def lifecycle_action(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        action = request.path_params["action"]

        if action not in ("enable", "disable"):
            return _error("not_found", f"unknown action '{action}' for '{slug}'", 404)

        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        enabled = action == "enable"
        token = in_command_context.set(True)
        try:
            updated, actions, warnings = await self.service.set_enabled(
                orm_type, row_id, enabled,
            )
        except StorageError as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if updated is None:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)
        if warnings:
            return _partial(updated, warnings)
        return _ok(updated, actions)

    # -- helpers --

    async def _read_json_body(self, request: Request) -> object | JSONResponse:
        try:
            return await request.json()
        except json.JSONDecodeError:
            return _error("validation_error", "invalid JSON body", 400)

    async def _decode_create_payload(
        self,
        orm_type: type[Model],
        raw_payload: object,
    ) -> msgspec.Struct | JSONResponse:
        if orm_type is ActorORM:
            request = _convert_request(raw_payload, ActorCreateRequest)
            if isinstance(request, JSONResponse):
                return request
            return await self._actor_record_from_create(request)

        if orm_type is IntegrationORM:
            request = _convert_request(raw_payload, IntegrationCreateRequest)
            if isinstance(request, JSONResponse):
                return request
            return await self._integration_record_from_create(request)

        if orm_type is ActorIngressRuleORM:
            request = _convert_request(raw_payload, ActorIngressRuleCreateRequest)
            if isinstance(request, JSONResponse):
                return request
            if not request.id:
                request.id = str(uuid.uuid4())
            return ActorIngressRuleRecord(
                id=request.id,
                actor_id=request.actor_id,
                source_id_pattern=request.source_id_pattern,
                source_path_pattern=request.source_path_pattern,
                kind_patterns=request.kind_patterns,
                enabled=request.enabled,
                version=request.version,
            )

        schema_type = schema_type_of(orm_type)
        record = _convert_request(raw_payload, schema_type)
        if isinstance(record, JSONResponse):
            return record
        _ensure_record_id(record)
        return record

    async def _decode_update_payload(
        self,
        orm_type: type[Model],
        row_id: str,
        raw_payload: object,
    ) -> dict[str, Any] | JSONResponse:
        if orm_type is ActorORM:
            request = _convert_request(raw_payload, ActorPatchRequest)
            if isinstance(request, JSONResponse):
                return request
            return await self._actor_fields_from_patch(request)

        if orm_type is IntegrationORM:
            request = _convert_request(raw_payload, IntegrationPatchRequest)
            if isinstance(request, JSONResponse):
                return request
            return await self._integration_fields_from_patch(row_id, request)

        patch_type = _patch_request_type(orm_type)
        if patch_type is None:
            return _error(
                "validation_error",
                f"{orm_type.__name__} does not support command updates",
                400,
            )
        patch = _convert_request(raw_payload, patch_type)
        if isinstance(patch, JSONResponse):
            return patch
        return _patch_fields(patch)

    async def _actor_record_from_create(
        self,
        request: ActorCreateRequest,
    ) -> ActorRecord | JSONResponse:
        character = await self._resolve_character(request.character_id, request.character)
        if isinstance(character, JSONResponse):
            return character
        if character is None:
            return _error("validation_error", "actor character must be set", 400)

        llm_backend = await self._resolve_llm_backend(
            request.llm_backend_id,
            request.llm_backend,
        )
        if isinstance(llm_backend, JSONResponse):
            return llm_backend
        if llm_backend is None:
            return _error("validation_error", "actor llm_backend must be set", 400)

        return ActorRecord(
            id=request.id or str(uuid.uuid4()),
            name=request.name,
            type=request.type,
            character=character,
            llm_backend=llm_backend,
            model=request.model,
            config=request.config,
            enabled=request.enabled,
            version=request.version,
            llm_options=_value_or(request.llm_options, YuuAgentLLMOptions()),
            budget=_actor_budget(request),
            agent_tools=_value_or(request.agent_tools, ()),
            allowed_capability_ids=_actor_capability_ids(request),
            runtime_policy=_actor_runtime_policy(request),
            resource_policy=_actor_resource_policy(request),
        )

    async def _actor_fields_from_patch(
        self,
        request: ActorPatchRequest,
    ) -> dict[str, Any] | JSONResponse:
        convenience_fields = frozenset(
            {
                "id",
                "character_id",
                "llm_backend_id",
                "max_steps",
                "memory_enabled",
                "workspace_access",
                "daily_budget",
                "capability_ids",
            }
        )
        fields = _patch_fields(request, exclude=convenience_fields)

        character = await self._resolve_character(request.character_id, request.character)
        if isinstance(character, JSONResponse):
            return character
        if character is not None:
            fields["character"] = character

        llm_backend = await self._resolve_llm_backend(
            request.llm_backend_id,
            request.llm_backend,
        )
        if isinstance(llm_backend, JSONResponse):
            return llm_backend
        if llm_backend is not None:
            fields["llm_backend"] = llm_backend

        if "budget" not in fields and request.max_steps is not msgspec.UNSET:
            fields["budget"] = _struct_fields(YuuAgentBudget(max_steps=request.max_steps))
        if (
            "allowed_capability_ids" not in fields
            and request.capability_ids is not msgspec.UNSET
        ):
            fields["allowed_capability_ids"] = request.capability_ids
        if "runtime_policy" not in fields and request.memory_enabled is not msgspec.UNSET:
            fields["runtime_policy"] = _struct_fields(
                RuntimePolicy(memory_enabled=request.memory_enabled)
            )
        if "resource_policy" not in fields and (
            request.workspace_access is not msgspec.UNSET
            or request.daily_budget is not msgspec.UNSET
        ):
            fields["resource_policy"] = _struct_fields(
                ResourcePolicy(
                    workspace_access=(
                        request.workspace_access
                        if request.workspace_access is not msgspec.UNSET
                        else "none"
                    ),
                    budget_usd_daily=(
                        request.daily_budget
                        if request.daily_budget is not msgspec.UNSET
                        else None
                    ),
                )
            )
        return fields

    async def _integration_record_from_create(
        self,
        request: IntegrationCreateRequest,
    ) -> IntegrationRecord | JSONResponse:
        config: dict[str, object] = {}
        if request.config is not msgspec.UNSET:
            wrapped = await self._wrap_integration_config(
                name=request.name,
                config=request.config,
                existing=None,
            )
            if isinstance(wrapped, JSONResponse):
                return wrapped
            config = wrapped
        return IntegrationRecord(
            id=request.id or str(uuid.uuid4()),
            name=request.name,
            config=config,
            enabled=request.enabled,
            version=request.version,
        )

    async def _integration_fields_from_patch(
        self,
        row_id: str,
        request: IntegrationPatchRequest,
    ) -> dict[str, Any] | JSONResponse:
        fields = _patch_fields(request)
        if request.config is msgspec.UNSET:
            return fields

        existing = await self.repository.get(IntegrationORM, row_id)
        if existing is None:
            return fields
        name = request.name if request.name is not msgspec.UNSET else existing.name
        wrapped = await self._wrap_integration_config(
            name=name,
            config=request.config,
            existing=existing.config,
        )
        if isinstance(wrapped, JSONResponse):
            return wrapped
        fields["config"] = wrapped
        return fields

    async def _wrap_integration_config(
        self,
        *,
        name: str,
        config: dict[str, object],
        existing: dict[str, object] | None,
    ) -> dict[str, object] | JSONResponse:
        if not name:
            return _error("validation_error", "integration name must be set", 400)
        try:
            factory = self.service.integrations.factories.get(name)
        except LookupError as exc:
            return _error("validation_error", str(exc), 400)
        try:
            return wrap_config_secrets(
                config,
                schema=factory.config_schema,
                existing=existing,
            )
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)

    async def _resolve_character(
        self,
        character_id: str | msgspec.UnsetType,
        character: CharacterRecord | msgspec.UnsetType,
    ) -> CharacterRecord | JSONResponse | None:
        if character is not msgspec.UNSET:
            return await self._existing_character(character.id)
        if character_id is not msgspec.UNSET and character_id:
            return await self._existing_character(character_id)
        return None

    async def _resolve_llm_backend(
        self,
        llm_backend_id: str | msgspec.UnsetType,
        llm_backend: LLMBackendRecord | msgspec.UnsetType,
    ) -> LLMBackendRecord | JSONResponse | None:
        if llm_backend is not msgspec.UNSET:
            return await self._existing_llm_backend(llm_backend.id)
        if llm_backend_id is not msgspec.UNSET and llm_backend_id:
            return await self._existing_llm_backend(llm_backend_id)
        return None

    async def _existing_character(self, character_id: str) -> CharacterRecord | JSONResponse:
        character = await self.repository.get(CharacterORM, character_id)
        if character is None:
            return _error("validation_error", f"character '{character_id}' not found", 400)
        return character

    async def _existing_llm_backend(self, backend_id: str) -> LLMBackendRecord | JSONResponse:
        llm_backend = await self.repository.get(LLMBackendORM, backend_id)
        if llm_backend is None:
            return _error("validation_error", f"llm_backend '{backend_id}' not found", 400)
        return llm_backend


def build_commands_app(
    service: ResourceService,
    type_registry: ResourceTypeRegistry,
    repository: ResourceRepository,
    config: ServerConfig,
) -> Starlette:
    handlers = ResourceCommandHandlers(
        service, type_registry, repository,
    )

    routes = [
        Route("/{resource_type}", handlers.create, methods=["POST"]),
        Route("/{resource_type}", handlers.list_all, methods=["GET"]),
        Route("/{resource_type}/{id}", handlers.get, methods=["GET"]),
        Route("/{resource_type}/{id}", handlers.update, methods=["PUT"]),
        Route("/{resource_type}/{id}", handlers.delete, methods=["DELETE"]),
        Route("/{resource_type}/{id}/{action}", handlers.lifecycle_action, methods=["POST"]),
    ]

    return Starlette(
        routes=routes,
        middleware=[Middleware(SecretMiddleware, secret=config.daemon_secret)],
    )
