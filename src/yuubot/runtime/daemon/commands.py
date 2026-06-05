"""Resource command sub-application for the daemon.

HTTP handlers parse/validate input and format output.
All business logic (CRUD, reconcile, lifecycle) is delegated to ResourceService.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from typing import Any, cast

import msgspec
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from tortoise import Model

from yuubot.bootstrap.config import ServerConfig
from yuubot.core.secrets import redact_secret_for_json, wrap_config_secrets
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
    validate_actor_references,
    validate_delete_not_referenced,
)

logger = logging.getLogger(__name__)

in_command_context: ContextVar[bool] = ContextVar("in_command_context", default=False)


# -- Request Structs: typed boundary for HTTP payloads --


class CreateActorRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for actor creation/update requests.

    Captures the simplified/flat form used by the admin UI and startup guide,
    where ``character_id``/``llm_backend_id`` and convenience fields like
    ``max_steps`` are expanded into nested structs by
    ``_normalize_actor_payload``.
    """

    id: str = ""
    name: str = ""
    type: str = "simple_loop"
    character_id: str = ""
    llm_backend_id: str = ""
    model: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    # Flattened convenience fields that map to nested structs
    max_steps: int | None = None
    memory_enabled: bool | None = None
    workspace_access: str = ""
    daily_budget: float | None = None
    capability_ids: list[str] = msgspec.field(default_factory=list)


class CreateIntegrationRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for integration creation/update requests."""

    id: str = ""
    name: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True


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


def _encode_record(record: object) -> Any:
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

        payload = await self._parse_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())

        config_error = await self._prepare_integration_config(orm_type, payload)
        if config_error is not None:
            return config_error

        if orm_type is ActorORM:
            payload = await self._normalize_actor_payload(payload)
            ref_error = await self._validate_actor_refs(payload)
            if ref_error is not None:
                return ref_error

        record = self._decode_payload(orm_type, payload)
        if isinstance(record, JSONResponse):
            return record

        token = in_command_context.set(True)
        try:
            inserted, actions, warnings = await self.service.create(orm_type, record)
        except ValueError as exc:
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

        payload = await self._parse_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        payload.pop("id", None)
        if not payload:
            return _error("validation_error", "no fields to update", 400)

        if orm_type is ActorORM:
            payload = await self._normalize_actor_payload(payload)
            ref_error = await self._validate_actor_refs(payload)
            if ref_error is not None:
                return ref_error

        config_error = await self._prepare_integration_config(
            orm_type,
            payload,
            row_id=row_id,
        )
        if config_error is not None:
            return config_error

        token = in_command_context.set(True)
        try:
            updated, actions, warnings = await self.service.update(
                orm_type, row_id, **payload,
            )
        except ValueError as exc:
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
        except ValueError as exc:
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
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if updated is None:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)
        if warnings:
            return _partial(updated, warnings)
        return _ok(updated, actions)

    # -- helpers --

    async def _parse_json_body(self, request: Request) -> dict[str, object] | JSONResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return _error("validation_error", "invalid JSON body", 400)
        if not isinstance(payload, dict):
            return _error("validation_error", "body must be a JSON object", 400)
        return payload  # type: ignore[return-value]

    def _decode_payload(self, orm_type: type[Model], payload: dict[str, object]) -> msgspec.Struct | JSONResponse:
        schema_type = schema_type_of(orm_type)
        try:
            return msgspec.convert(payload, type=schema_type, strict=False)
        except (msgspec.ValidationError, msgspec.DecodeError) as exc:
            return _error("validation_error", str(exc), 400)

    async def _validate_actor_refs(self, payload: dict[str, object]) -> JSONResponse | None:
        try:
            await validate_actor_references(payload, self.repository)
        except ValidationError as exc:
            return _error(exc.code, exc.detail, 400)
        return None

    async def _normalize_actor_payload(self, payload: dict[str, object]) -> dict[str, object]:
        """Accept the simplified actor form used by the admin UI/startup guide.

        Parses the raw dict into ``CreateActorRequest`` for typed field access,
        then performs FK resolution and expands convenience fields into the
        nested structs expected by ``ActorRecord``.
        """
        req = msgspec.convert(payload, type=CreateActorRequest, strict=False)

        # Start from the original payload, removing convenience keys that
        # are expanded into nested structs below.
        normalized = {k: v for k, v in payload.items()
                      if k not in {"character_id", "llm_backend_id", "max_steps",
                                   "memory_enabled", "workspace_access",
                                   "daily_budget", "capability_ids"}}

        # FK resolution — character_id / llm_backend_id → full objects
        if req.character_id and "character" not in normalized:
            character = await self.repository.get(CharacterORM, req.character_id)
            if character is not None:
                normalized["character"] = msgspec.to_builtins(character)
        if req.llm_backend_id and "llm_backend" not in normalized:
            llm_backend = await self.repository.get(LLMBackendORM, req.llm_backend_id)
            if llm_backend is not None:
                normalized["llm_backend"] = msgspec.to_builtins(llm_backend)

        # Nested struct defaults — only set when absent from the payload
        if "llm_options" not in normalized:
            normalized["llm_options"] = {}
        if "budget" not in normalized:
            budget: dict[str, object] = {}
            if req.max_steps is not None:
                budget["max_steps"] = req.max_steps
            normalized["budget"] = budget
        if "agent_tools" not in normalized:
            normalized["agent_tools"] = []
        if "allowed_capability_ids" not in normalized:
            normalized["allowed_capability_ids"] = req.capability_ids
        if "runtime_policy" not in normalized:
            runtime_policy: dict[str, object] = {}
            if req.memory_enabled is not None:
                runtime_policy["memory_enabled"] = req.memory_enabled
            normalized["runtime_policy"] = runtime_policy
        if "resource_policy" not in normalized:
            resource_policy: dict[str, object] = {}
            if req.workspace_access:
                resource_policy["workspace_access"] = req.workspace_access
            if req.daily_budget is not None:
                resource_policy["budget_usd_daily"] = req.daily_budget
            normalized["resource_policy"] = resource_policy

        return normalized

    async def _prepare_integration_config(
        self,
        orm_type: type[Model],
        payload: dict[str, object],
        *,
        row_id: str | None = None,
    ) -> JSONResponse | None:
        if orm_type is not IntegrationORM or "config" not in payload:
            return None
        config = payload["config"]
        if not isinstance(config, dict):
            return _error("validation_error", "integration config must be an object", 400)
        config = cast(dict[str, object], config)

        existing = None
        if row_id is not None:
            existing = await self.repository.get(IntegrationORM, row_id)
            if existing is None:
                return None

        req = msgspec.convert(payload, type=CreateIntegrationRequest, strict=False)
        name = req.name or (existing.name if existing is not None else "")
        if not name:
            return _error("validation_error", "integration name must be set", 400)

        try:
            factory = self.service.integrations.factories.get(name)
        except LookupError as exc:
            return _error("validation_error", str(exc), 400)

        try:
            payload["config"] = wrap_config_secrets(
                config,
                schema=factory.config_schema,
                existing=existing.config if existing is not None else None,
            )
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        return None


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
