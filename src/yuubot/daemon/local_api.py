"""Daemon-local API used by RFC2 kernel clients."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import msgspec

from yuubot.config import Config
from yuubot.daemon.runtime import KernelTokenBinding, resolve_kernel_token
from yuubot.services.base import AccessDenied, YuubotServiceError
from yuubot.services.delegate import DelegateService
from yuubot.services.im import ImService
from yuubot.services.media import MediaService
from yuubot.services.mem import MemoryService
from yuubot.services.schedule import ScheduleService
from yuubot.services.web import WebService
from yuubot.services.workspace import WorkspaceService


ServiceHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


def _service_name(svc: object) -> str:
    """Derive the route prefix for a service instance."""
    explicit = getattr(type(svc), "_service_name", None)
    if explicit:
        return str(explicit)
    cls_name = type(svc).__name__
    if cls_name.endswith("Service"):
        cls_name = cls_name[: -len("Service")]
    return cls_name.lower()


def _register(handlers: dict[tuple[str, str], ServiceHandler], svc: object) -> None:
    prefix = _service_name(svc)
    for name, method in inspect.getmembers(svc, predicate=inspect.iscoroutinefunction):
        if not name.startswith("_"):
            handlers[(prefix, name)] = method


def create_agent_fn_router(
    *,
    config: Config | None = None,
    agent_runner: object | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/agent-fns")
    im = ImService(config=config)
    mem = MemoryService(config=config)
    web = WebService(config=config)
    schedule = ScheduleService(config=config)
    media = MediaService(config=config)
    workspace = WorkspaceService()
    delegate = DelegateService(runner=agent_runner)  # type: ignore[arg-type]

    handlers: dict[tuple[str, str], ServiceHandler] = {}
    for svc in (im, mem, web, schedule, media, workspace, delegate):
        _register(handlers, svc)

    @router.post("/scope/check")
    async def scope_check(request: Request) -> JSONResponse:
        try:
            binding = _binding_from_request(request)
        except AccessDenied as exc:
            return JSONResponse(exc.to_dict(), status_code=403)
        return JSONResponse({"status": "ok", "binding": _binding_dict(binding)})

    @router.post("/{service}/{action}")
    async def service_action(service: str, action: str, request: Request) -> JSONResponse:
        handler = handlers.get((service, action))
        if handler is None:
            return JSONResponse(
                {"code": "not_found", "message": f"unknown agent service: {service}/{action}"},
                status_code=404,
            )
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                payload = {}
            binding = _binding_from_request(request, required=False)
            enriched = _enrich_payload(payload, binding)
            result = await handler(enriched)
            return JSONResponse(msgspec.to_builtins(result))
        except AccessDenied as exc:
            return JSONResponse(exc.to_dict(), status_code=403)
        except YuubotServiceError as exc:
            return JSONResponse(exc.to_dict(), status_code=400)

    return router


def _binding_from_request(request: Request, *, required: bool = True) -> KernelTokenBinding | None:
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        token = request.headers.get("X-Yuubot-Agent-Token", "").strip()
    binding = resolve_kernel_token(token) if token else None
    if required and binding is None:
        raise AccessDenied("invalid or missing kernel token")
    return binding


def _binding_dict(binding: KernelTokenBinding | None) -> dict[str, Any] | None:
    if binding is None:
        return None
    return {
        "bot_kind": binding.bot_kind,
        "agent_id": binding.agent_id,
        "ctx_id": binding.ctx_id,
        "group_id": binding.group_id,
        "user_id": binding.user_id,
        "conversation_id": binding.conversation_id,
        "character_name": binding.character_name,
        "task_id": binding.task_id,
    }


def _enrich_payload(payload: Mapping[str, Any], binding: KernelTokenBinding | None) -> dict[str, Any]:
    enriched = dict(payload)
    if binding is None:
        return enriched
    enriched.setdefault("bot_kind", binding.bot_kind)
    enriched.setdefault("agent_id", binding.agent_id)
    enriched.setdefault("ctx_id", binding.ctx_id)
    enriched.setdefault("group_id", binding.group_id)
    enriched.setdefault("user_id", binding.user_id)
    enriched.setdefault("conversation_id", binding.conversation_id)
    enriched.setdefault("character_name", binding.character_name)
    enriched.setdefault("agent_name", binding.character_name)
    enriched.setdefault("task_id", binding.task_id)
    return enriched
