"""Resource payload encoding/decoding for HTTP command handlers.

Extracted from ResourceCommandHandlers to keep each file focused
and under the 400-line ceiling.  ResourceCodec owns the decode
logic; handlers own the HTTP/REST lifecycle.
"""

from __future__ import annotations

import uuid
from typing import Any

import msgspec
from starlette.responses import JSONResponse
from tortoise import Model

from yuubot.core.secrets import wrap_config_secrets
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    CharacterRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ResourcePolicy,
    RuntimePolicy,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.service import ResourceService
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CharacterORM,
    IntegrationORM,
    LLMBackendORM,
)
from yuubot.resources.store.protocol import schema_type_of
from yuubot.runtime.daemon.commands._helpers import (
    _actor_budget,
    _actor_capability_ids,
    _actor_resource_policy,
    _actor_runtime_policy,
    _convert_request,
    _ensure_record_id,
    _error,
    _patch_fields,
    _patch_request_type,
    _struct_fields,
    _value_or,
)
from yuubot.runtime.daemon.commands._schemas import (
    ActorCreateRequest,
    ActorIngressRuleCreateRequest,
    ActorPatchRequest,
    IntegrationCreateRequest,
    IntegrationPatchRequest,
)


class ResourceCodec:
    """Decodes HTTP request payloads into resource records and update fields.

    Dependencies are injected at construction time so decode logic
    can be tested independently of the HTTP handler layer.
    """

    def __init__(
        self,
        repository: ResourceRepository,
        service: ResourceService,
    ):
        self._repository = repository
        self._service = service

    # -- public decode entry points --

    async def decode_create_payload(
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

    async def decode_update_payload(
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

    # -- actor helpers --

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

        pricing_error = self._validate_actor_pricing(request, llm_backend)
        if pricing_error is not None:
            return pricing_error

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

    # -- integration helpers --

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

        existing = await self._repository.get(IntegrationORM, row_id)
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
            factory = self._service.integrations.factories.get(name)
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

    # -- actor validation --

    def _validate_actor_pricing(
        self,
        request: ActorCreateRequest,
        llm_backend: LLMBackendRecord,
    ) -> JSONResponse | None:
        """Reject actors with budgets that have no pricing entry for their model."""
        budget = _actor_budget(request)
        requires_pricing = (
            budget.max_usd > 0
            or (llm_backend.budget.daily_usd is not None and llm_backend.budget.daily_usd > 0)
            or (llm_backend.budget.monthly_usd is not None and llm_backend.budget.monthly_usd > 0)
        )
        if not requires_pricing:
            return None
        model = request.model
        if model and not any(e.model == model for e in llm_backend.pricing.entries):
            return _error(
                "configuration_error",
                f"actor {request.name!r}: USD budget requires pricing for "
                f"model {model!r} in backend {llm_backend.name!r}",
                400,
            )
        return None

    # -- reference resolution --

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
        character = await self._repository.get(CharacterORM, character_id)
        if character is None:
            return _error("validation_error", f"character '{character_id}' not found", 400)
        return character

    async def _existing_llm_backend(self, backend_id: str) -> LLMBackendRecord | JSONResponse:
        llm_backend = await self._repository.get(LLMBackendORM, backend_id)
        if llm_backend is None:
            return _error("validation_error", f"llm_backend '{backend_id}' not found", 400)
        return llm_backend
