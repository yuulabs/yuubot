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
from yuubot.core.tools import ToolRegistry
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    CapabilitySetRecord,
    CharacterRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ToolConfig,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.service import ResourceService
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CapabilitySetORM,
    CharacterORM,
    IntegrationORM,
    LLMBackendORM,
)
from yuubot.resources.store.protocol import schema_type_of
from yuubot.runtime.daemon.commands._helpers import (
    _convert_request,
    _ensure_record_id,
    _error,
    _patch_fields,
    _patch_request_type,
    _value_or,
)
from yuubot.runtime.daemon.commands._schemas import (
    ActorCreateRequest,
    ActorIngressRuleCreateRequest,
    ActorPatchRequest,
    CapabilitySetPatchRequest,
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
        *,
        tool_registry: ToolRegistry | None = None,
    ):
        self._repository = repository
        self._service = service
        self._tool_registry = tool_registry

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
        if isinstance(record, CapabilitySetRecord):
            error = self._validate_agent_tools(record.agent_tools)
            if error is not None:
                return error
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
        fields = _patch_fields(patch)
        if isinstance(patch, CapabilitySetPatchRequest):
            tools = fields.get("agent_tools")
            if isinstance(tools, tuple):
                error = self._validate_agent_tools(tools)
                if error is not None:
                    return error
        return fields

    # -- agent tools validation --

    async def _actor_record_from_create(
        self,
        request: ActorCreateRequest,
    ) -> ActorRecord | JSONResponse:
        character = await self._existing_character(request.default_character_id)
        if isinstance(character, JSONResponse):
            return character

        capability_set = await self._existing_capability_set(request.capability_set_id)
        if isinstance(capability_set, JSONResponse):
            return capability_set

        llm_backend = await self._existing_llm_backend(request.default_llm_backend_id)
        if isinstance(llm_backend, JSONResponse):
            return llm_backend

        pricing_error = self._validate_actor_pricing(request, llm_backend)
        if pricing_error is not None:
            return pricing_error

        return ActorRecord(
            id=request.id or str(uuid.uuid4()),
            name=request.name,
            type=request.type,
            default_character=character,
            capability_set=capability_set,
            default_llm_backend=llm_backend,
            default_model=request.default_model,
            config=request.config,
            enabled=request.enabled,
            version=request.version,
            default_llm_options=_value_or(
                request.default_llm_options, YuuAgentLLMOptions()
            ),
            default_budget=_value_or(request.default_budget, YuuAgentBudget()),
        )

    async def _actor_fields_from_patch(
        self,
        request: ActorPatchRequest,
    ) -> dict[str, Any] | JSONResponse:
        convenience_fields = frozenset(
            {
                "id",
                "default_character_id",
                "capability_set_id",
                "default_llm_backend_id",
            }
        )
        fields = _patch_fields(request, exclude=convenience_fields)

        if request.default_character_id is not msgspec.UNSET:
            character = await self._existing_character(request.default_character_id)
            if isinstance(character, JSONResponse):
                return character
            fields["default_character"] = character
        if request.capability_set_id is not msgspec.UNSET:
            capability_set = await self._existing_capability_set(request.capability_set_id)
            if isinstance(capability_set, JSONResponse):
                return capability_set
            fields["capability_set"] = capability_set
        if request.default_llm_backend_id is not msgspec.UNSET:
            llm_backend = await self._existing_llm_backend(
                request.default_llm_backend_id
            )
            if isinstance(llm_backend, JSONResponse):
                return llm_backend
            fields["default_llm_backend"] = llm_backend
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
        budget = _value_or(request.default_budget, YuuAgentBudget())
        requires_pricing = (
            budget.max_usd > 0
            or (llm_backend.budget.daily_usd is not None and llm_backend.budget.daily_usd > 0)
            or (llm_backend.budget.monthly_usd is not None and llm_backend.budget.monthly_usd > 0)
        )
        if not requires_pricing:
            return None
        model = request.default_model
        if model and not any(e.model == model for e in llm_backend.pricing.entries):
            return _error(
                "configuration_error",
                f"actor {request.name!r}: USD budget requires pricing for "
                f"model {model!r} in backend {llm_backend.name!r}",
                400,
            )
        return None

    # -- reference resolution --

    async def _existing_character(self, character_id: str) -> CharacterRecord | JSONResponse:
        character = await self._repository.get(CharacterORM, character_id)
        if character is None:
            return _error("validation_error", f"character '{character_id}' not found", 400)
        return character

    async def _existing_capability_set(
        self, capability_set_id: str
    ) -> CapabilitySetRecord | JSONResponse:
        capability_set = await self._repository.get(CapabilitySetORM, capability_set_id)
        if capability_set is None:
            return _error(
                "validation_error",
                f"capability_set '{capability_set_id}' not found",
                400,
            )
        return capability_set

    async def _existing_llm_backend(self, backend_id: str) -> LLMBackendRecord | JSONResponse:
        llm_backend = await self._repository.get(LLMBackendORM, backend_id)
        if llm_backend is None:
            return _error("validation_error", f"llm_backend '{backend_id}' not found", 400)
        return llm_backend

    # -- agent tools validation --

    def _validate_agent_tools(
        self,
        agent_tools: tuple[ToolConfig, ...],
    ) -> JSONResponse | None:
        """Validate that every tool_name references a registered tool type."""
        if self._tool_registry is None:
            return None
        for tool in agent_tools:
            try:
                self._tool_registry.get(tool.tool_name)
            except LookupError:
                return _error(
                    "validation_error",
                    f"Unknown tool type {tool.tool_name!r} — "
                    f"available: {sorted(self._tool_registry._factories)!r}",
                    400,
                )
        return None
