"""Resource command sub-application for the daemon."""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any, Protocol, cast

import msgspec
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from tortoise import Model

from yuubot.bootstrap.config import ServerConfig
from yuubot.core.actors import ActorManager
from yuubot.core.integrations import IntegrationCore
from yuubot.resources.events import ResourceAction, ResourceChanged
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CharacterORM,
    IntegrationORM,
    LLMBackendORM,
    PromptTemplateORM,
    SecretORM,
)
from yuubot.runtime.validators import (
    ValidationError,
    validate_actor_references,
    validate_delete_not_referenced,
)

logger = logging.getLogger(__name__)


class RefreshDispatcher(Protocol):
    async def refresh(self, event: ResourceChanged) -> list[str]: ...

logger = logging.getLogger(__name__)

in_command_context: ContextVar[bool] = ContextVar("in_command_context", default=False)

_encoder = msgspec.json.Encoder()

RESOURCE_REGISTRY: dict[str, type[Model]] = {
    "llm-backends": LLMBackendORM,
    "integrations": IntegrationORM,
    "characters": CharacterORM,
    "actors": ActorORM,
    "ingress-rules": ActorIngressRuleORM,
    "prompt-templates": PromptTemplateORM,
    "secrets": SecretORM,
}


def _encode_record(record: object) -> Any:
    return msgspec.json.decode(_encoder.encode(record))


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
    """CRUD + lifecycle handlers for all resource types."""

    def __init__(
        self,
        repository: ResourceRepository,
        refresh: RefreshDispatcher,
        integrations: IntegrationCore,
        actors: ActorManager,
    ):
        self.repository = repository
        self.refresh = refresh
        self.integrations = integrations
        self.actors = actors

    async def create(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        orm_type = RESOURCE_REGISTRY.get(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        try:
            payload = await request.json()
        except Exception:
            return _error("validation_error", "invalid JSON body", 400)

        if not isinstance(payload, dict):
            return _error("validation_error", "body must be a JSON object", 400)

        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())

        schema_type = getattr(orm_type, "_yuubot_schema_type")
        try:
            record = msgspec.convert(payload, type=schema_type, strict=False)
        except (msgspec.ValidationError, msgspec.DecodeError) as exc:
            return _error("validation_error", str(exc), 400)

        if orm_type is ActorORM:
            try:
                await validate_actor_references(payload, self.repository)
            except ValidationError as exc:
                return _error(exc.code, exc.detail, 400)

        token = in_command_context.set(True)
        try:
            inserted = await self.repository.insert(orm_type, record)
        except Exception as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        actions, warnings = await self._reconcile(orm_type, "inserted", payload["id"])
        if warnings:
            return _partial(inserted, warnings)
        return _ok(inserted, actions, status_code=201)

    async def get(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        orm_type = RESOURCE_REGISTRY.get(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        record = await self.repository.get(orm_type, row_id)
        if record is None:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)
        return _ok(record)

    async def list_all(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        orm_type = RESOURCE_REGISTRY.get(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        records = await self.repository.list(orm_type)
        return JSONResponse(
            {"status": "ok", "data": [_encode_record(r) for r in records]},
        )

    async def update(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        orm_type = RESOURCE_REGISTRY.get(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        try:
            payload = await request.json()
        except Exception:
            return _error("validation_error", "invalid JSON body", 400)

        if not isinstance(payload, dict):
            return _error("validation_error", "body must be a JSON object", 400)

        payload.pop("id", None)
        if not payload:
            return _error("validation_error", "no fields to update", 400)

        if orm_type is ActorORM:
            try:
                await validate_actor_references(payload, self.repository)
            except ValidationError as exc:
                return _error(exc.code, exc.detail, 400)

        token = in_command_context.set(True)
        try:
            updated = await self.repository.update(orm_type, row_id, **payload)
        except Exception as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if updated is None:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)

        actions, warnings = await self._reconcile(
            orm_type, "updated", row_id, tuple(payload.keys())
        )
        if warnings:
            return _partial(updated, warnings)
        return _ok(updated, actions)

    async def delete(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        orm_type = RESOURCE_REGISTRY.get(slug)
        if orm_type is None:
            return _error("not_found", f"unknown resource type '{slug}'", 404)

        try:
            await validate_delete_not_referenced(orm_type, row_id, self.repository)
        except ValidationError as exc:
            return _error(exc.code, exc.detail, 409)

        token = in_command_context.set(True)
        try:
            deleted = await self.repository.delete(orm_type, row_id)
        except Exception as exc:
            return _error("validation_error", str(exc), 400)
        finally:
            in_command_context.reset(token)

        if not deleted:
            return _error("not_found", f"{slug} '{row_id}' not found", 404)

        actions, warnings = await self._reconcile(orm_type, "deleted", row_id)
        return JSONResponse(
            {"status": "ok", "actions": list(actions), "warnings": warnings},
        )

    async def lifecycle_action(self, request: Request) -> JSONResponse:
        slug = request.path_params["resource_type"]
        row_id = request.path_params["id"]
        action = request.path_params["action"]

        if slug == "integrations" and action in ("enable", "disable"):
            return await self._integration_lifecycle(row_id, action)
        if slug == "actors" and action in ("enable", "disable"):
            return await self._actor_lifecycle(row_id, action)

        return _error("not_found", f"unknown action '{action}' for '{slug}'", 404)

    async def _integration_lifecycle(self, row_id: str, action: str) -> JSONResponse:
        enabled = action == "enable"
        token = in_command_context.set(True)
        try:
            updated = await self.repository.update(IntegrationORM, row_id, enabled=enabled)
        finally:
            in_command_context.reset(token)

        if updated is None:
            return _error("not_found", f"integration '{row_id}' not found", 404)

        try:
            await self.integrations.reconcile(
                ResourceChanged(
                    table="integrations",
                    action="updated",
                    row_ids=(row_id,),
                    changed_fields=("enabled",),
                )
            )
            return _ok(updated, actions=[f"integration.{action}d"])
        except Exception as exc:
            logger.exception("integration %s failed for %s", action, row_id)
            return _partial(updated, [f"integration {action} failed: {exc}"])

    async def _actor_lifecycle(self, row_id: str, action: str) -> JSONResponse:
        enabled = action == "enable"
        token = in_command_context.set(True)
        try:
            updated = await self.repository.update(ActorORM, row_id, enabled=enabled)
        finally:
            in_command_context.reset(token)

        if updated is None:
            return _error("not_found", f"actor '{row_id}' not found", 404)

        try:
            actions = await self.refresh.refresh(
                ResourceChanged(
                    table="actors",
                    action="updated",
                    row_ids=(row_id,),
                    changed_fields=("enabled",),
                )
            )
            return _ok(updated, actions=[f"actor.{action}d", *actions])
        except Exception as exc:
            logger.exception("actor %s failed for %s", action, row_id)
            return _partial(updated, [f"actor {action} failed: {exc}"])

    async def _reconcile(
        self,
        orm_type: type[Model],
        action: str,
        row_id: str,
        changed_fields: tuple[str, ...] = (),
    ) -> tuple[list[str], list[str]]:
        table = orm_type._meta.db_table
        event = ResourceChanged(
            table=table,
            action=cast(ResourceAction, action),
            row_ids=(row_id,),
            changed_fields=changed_fields,
        )
        try:
            actions = await self.refresh.refresh(event)
            return actions, []
        except Exception as exc:
            logger.exception("reconcile failed after %s on %s", action, table)
            return [], [str(exc)]


def build_commands_app(
    repository: ResourceRepository,
    refresh: RefreshDispatcher,
    integrations: IntegrationCore,
    actors: ActorManager,
    config: ServerConfig,
) -> Starlette:
    handlers = ResourceCommandHandlers(repository, refresh, integrations, actors)

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
