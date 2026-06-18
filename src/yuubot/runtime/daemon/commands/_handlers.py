"""HTTP resource command handlers — CRUD and lifecycle.

Each handler parses/validates the request, delegates business logic
to ResourceService, and formats the JSON response.  Decode logic
lives in ResourceCodec to keep this file under the 400-line ceiling.
"""

from __future__ import annotations

import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from yuubot.core.tools import ToolRegistry
from yuubot.resources.errors import StorageError
from yuubot.resources.registry import ResourceTypeRegistry
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.service import ResourceService
from yuubot.runtime.daemon.commands._codec import ResourceCodec
from yuubot.runtime.daemon.commands._helpers import (
    _encode_record,
    _error,
    _ok,
    _partial,
)
from yuubot.runtime.daemon.commands._schemas import in_command_context
from yuubot.runtime.daemon.validators import (
    ValidationError,
    validate_delete_not_referenced,
)

logger = logging.getLogger(__name__)


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
        *,
        tool_registry: ToolRegistry | None = None,
    ):
        self.service = service
        self.type_registry = type_registry
        self.repository = repository
        self._codec = ResourceCodec(
            repository, service, tool_registry=tool_registry,
        )

    # -- CRUD --

    async def create(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        orm_type = self.type_registry.get_orm_type(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        raw_payload = await self._read_json_body(request)
        if isinstance(raw_payload, JSONResponse):
            return raw_payload

        record = await self._codec.decode_create_payload(orm_type, raw_payload)
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

        fields = await self._codec.decode_update_payload(orm_type, row_id, raw_payload)
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

    # -- lifecycle --

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
