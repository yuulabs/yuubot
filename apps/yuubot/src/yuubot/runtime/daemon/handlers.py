"""Daemon HTTP route handlers extracted from app.py.

Each handler is returned by a factory function that receives its
dependencies explicitly — no closure capture, no hidden state.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import cast

import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.datastructures import UploadFile
import yuullm

from yuubot.bootstrap.config import ServerConfig
from yuubot.core.actors import ActorManager
from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.assembly._history_codec import decode_prompt_item
from yuubot.core.conversation_events import ConversationSSEHeartbeat
from yuubot.core.conversations import (
    ConversationBindingConflict,
    ConversationManager,
    ConversationSendBinding,
    ConversationUploadBinding,
    ConversationUploadedFile,
)
from yuubot.resources.records import ConversationRecord
from yuubot.core.integrations import IntegrationCore
from yuubot.core.skills import actor_skills_view, delete_local_skill, import_global_skill
from yuubot.core.validation import ConfigurationError
from yuubot.resources.events import ResourceChanged
from yuubot.resources.registry import EventDrivenRefreshDispatcher
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, ActorORM, IntegrationORM
from yuubot.runtime.http_utils import error_response
from yuubot.runtime.plugin_manager import (
    ExternalPluginInboundMessage,
    ExternalPluginIntegration,
    ExternalPluginManager,
)
from yuubot.runtime.process import ServiceHost, TraceService

logger = logging.getLogger(__name__)

# -- Shared request structs (was in app.py) --


class ConversationMessageRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for conversation message send requests.

    ``text`` is the user message body required on every send. ``actor_id``
    (and friends) are consumed on the first send only and ignored on
    subsequent sends, where the persisted binding is authoritative.
    """

    text: str = ""
    message_id: str = ""
    actor_id: str = ""
    capability_set_id: str = ""
    llm_backend_id: str = ""
    model: str = ""
    uploads: list[ConversationUploadedFile] = msgspec.field(default_factory=list)


class SkillImportRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str


# --
# Utility helpers (extracted from app.py)
# --


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sse_event(event_type: str, data: object) -> str:
    """Format a Server-Sent Events frame."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"


def _configuration_error_response(exc: ConfigurationError) -> JSONResponse:
    return error_response(
        str(exc),
        status_code=400,
        code="configuration_error",
        hint=(
            "Configure pricing.entries for the selected LLM backend model "
            "or disable the USD budget before chatting."
        ),
    )


def _conversation_metadata(conversation: ConversationRecord) -> dict[str, object]:
    return {
        "conversation_id": conversation.conversation_id,
        "title": conversation.title,
        "actor_id": conversation.actor_id,
        "created_at": _iso_or_none(conversation.created_at),
        "updated_at": _iso_or_none(conversation.updated_at),
    }


def _conversation_conflict_response(
    exc: ConversationBindingConflict,
) -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "code": "conversation_binding_conflict",
            "detail": str(exc),
            "data": _conversation_metadata(exc.conversation),
        },
        status_code=409,
    )


# -- Payload parsing helpers --


async def _resource_changed_from_request(
    request: Request,
) -> ResourceChanged | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return error_response("request body must be a JSON object", status_code=400)
    try:
        return ResourceChanged.from_dict(cast(dict[str, object], payload))
    except ValueError as exc:
        return error_response(str(exc), status_code=400)


async def _conversation_message_request_from_request(
    request: Request,
) -> ConversationMessageRequest | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return error_response("request body must be a JSON object", status_code=400)

    try:
        req = msgspec.convert(payload, type=ConversationMessageRequest, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError):
        return error_response("invalid request body", status_code=400)

    if not req.text.strip() and not req.uploads:
        return error_response("text or uploads must be provided", status_code=400)
    return req


async def _conversation_upload_request_from_request(
    request: Request,
) -> tuple[ConversationUploadBinding, list[tuple[str, bytes, str]]] | JSONResponse:
    try:
        form = await request.form()
    except Exception:
        logger.exception("failed to parse conversation upload request")
        return error_response(
            "request body must be multipart/form-data",
            status_code=400,
        )

    binding = ConversationUploadBinding(actor_id=str(form.get("actor_id") or ""))
    files: list[tuple[str, bytes, str]] = []
    for value in form.getlist("files"):
        if not isinstance(value, UploadFile):
            continue
        content = await value.read()
        files.append(
            (
                value.filename or "upload",
                content,
                value.content_type or "application/octet-stream",
            )
        )
    if not files:
        return error_response("at least one file must be provided", status_code=400)
    return binding, files


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


async def _plugin_ingest_from_request(
    request: Request,
    plugin_manager: ExternalPluginManager,
) -> ExternalPluginInboundMessage | JSONResponse:
    token = _bearer_token(request)
    if token is None:
        return error_response("Authorization bearer token is missing", status_code=403)
    try:
        expected_integration_id = plugin_manager.integration_id_for_token(token)
    except PermissionError as exc:
        return error_response(str(exc), status_code=403)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return error_response("request body must be a JSON object", status_code=400)
    try:
        message = msgspec.convert(
            payload,
            type=ExternalPluginInboundMessage,
            strict=False,
        )
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        return error_response(str(exc), status_code=400)
    if message.integration_id != expected_integration_id:
        return error_response(
            "integration_id does not match plugin token", status_code=403
        )
    return message


def _resolve_external_plugin_instance(
    integrations: IntegrationCore,
    integration_id: str,
) -> ExternalPluginIntegration:
    instance = integrations.running_instance(integration_id)
    if not isinstance(instance, ExternalPluginIntegration):
        raise LookupError(f"integration {integration_id!r} is not an external plugin")
    return instance


# --
# Route handler factories
# Each returns an ``async def (request) -> Response`` callable ready
# to be passed to ``starlette.routing.Route``.
# --


def make_health_handler(
    config: ServerConfig,
    resources: Resources,
    plugin_manager: ExternalPluginManager,
):
    async def health(_: Request) -> JSONResponse:
        ingress_rules = await resources.repository.list(ActorIngressRuleORM)
        integrations = await resources.repository.list(IntegrationORM)
        return JSONResponse(
            {
                "status": "ok",
                "daemon": f"{config.daemon_host}:{config.daemon_port}",
                "ingress_rules": len(ingress_rules),
                "integrations": len(integrations),
                "external_plugins": len(plugin_manager.statuses()),
            }
        )

    return health


def make_status_handler(
    services: ServiceHost,
    actors: ActorManager,
    integrations: IntegrationCore,
    plugin_manager: ExternalPluginManager,
    gateway,
    trace_service: TraceService,
):
    async def status(request: Request) -> JSONResponse:
        body: dict[str, object] = {
            "status": "running" if services.started else "stopped",
            "running_integration_ids": integrations.running_integration_ids(),
            "running_actor_ids": actors.running_actor_ids(),
            "actor_workspaces": actors.running_actor_workspace_paths(),
            "route_binding_count": gateway.routes.binding_count(),
            "trace": {
                "enabled": trace_service.config.enabled,
                "status": trace_service.status,
            },
        }
        plugin_statuses = plugin_manager.statuses()
        if plugin_statuses:
            body["external_plugins"] = [
                {
                    "name": s.name,
                    "integration_id": s.integration_id,
                    "port": s.port,
                    "healthy": s.healthy,
                    "pid": s.pid,
                }
                for s in plugin_statuses
            ]
        return JSONResponse(body)

    return status


def make_refresh_handler(
    refresh: EventDrivenRefreshDispatcher,
):
    async def refresh_resources(request: Request) -> JSONResponse:
        event_or_response = await _resource_changed_from_request(request)
        if isinstance(event_or_response, JSONResponse):
            return event_or_response

        event = event_or_response
        try:
            actions = await refresh.refresh(event)
        except ConfigurationError as exc:
            return _configuration_error_response(exc)
        return JSONResponse(
            {
                "status": "ok",
                "event": event.to_dict(),
                "actions": list(actions),
            }
        )

    return refresh_resources


def make_actor_skills_handler(
    resources: Resources,
    *,
    global_skills_path: Path,
    workspace_root: Path,
):
    resolver = ActorWorkspaceResolver(workspace_root)

    async def actor_skills(request: Request) -> JSONResponse:
        actor_id = request.path_params["actor_id"]
        actor = await resources.repository.get(ActorORM, actor_id)
        if actor is None:
            return error_response("actor does not exist", status_code=404)
        view = actor_skills_view(
            global_root=global_skills_path,
            actor_workspace=resolver.resolve(actor_id),
            scope=actor.skill_scope,
        )
        return JSONResponse({"status": "ok", "data": msgspec.to_builtins(view)})

    return actor_skills


def make_import_actor_skill_handler(
    resources: Resources,
    *,
    global_skills_path: Path,
    workspace_root: Path,
):
    resolver = ActorWorkspaceResolver(workspace_root)

    async def import_actor_skill(request: Request) -> JSONResponse:
        actor_id = request.path_params["actor_id"]
        actor = await resources.repository.get(ActorORM, actor_id)
        if actor is None:
            return error_response("actor does not exist", status_code=404)
        try:
            payload = await request.json()
            import_request = msgspec.convert(
                payload,
                type=SkillImportRequest,
                strict=False,
            )
            skill = import_global_skill(
                global_root=global_skills_path,
                actor_workspace=resolver.resolve(actor_id),
                skill_name=import_request.name,
            )
        except (json.JSONDecodeError, ValueError, msgspec.ValidationError, msgspec.DecodeError) as exc:
            return error_response(str(exc), status_code=400)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        return JSONResponse({"status": "ok", "data": msgspec.to_builtins(skill)})

    return import_actor_skill


def make_delete_actor_skill_handler(
    resources: Resources,
    *,
    workspace_root: Path,
):
    resolver = ActorWorkspaceResolver(workspace_root)

    async def delete_actor_skill(request: Request) -> JSONResponse:
        actor_id = request.path_params["actor_id"]
        skill_name = request.path_params["skill_name"]
        actor = await resources.repository.get(ActorORM, actor_id)
        if actor is None:
            return error_response("actor does not exist", status_code=404)
        try:
            deleted = delete_local_skill(
                actor_workspace=resolver.resolve(actor_id),
                skill_name=skill_name,
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if not deleted:
            return error_response("local skill does not exist", status_code=404)
        return JSONResponse({"status": "ok"})

    return delete_actor_skill


def make_plugin_ingest_handler(
    plugin_manager: ExternalPluginManager,
    integrations: IntegrationCore,
):
    async def plugin_ingest(request: Request) -> JSONResponse:
        payload_or_response = await _plugin_ingest_from_request(request, plugin_manager)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response
        payload = payload_or_response

        try:
            instance = _resolve_external_plugin_instance(
                integrations,
                payload.integration_id,
            )
            message = await instance.emit_payload(payload)
        except PermissionError as exc:
            return error_response(str(exc), status_code=403)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

        return JSONResponse(
            {
                "status": "ok",
                "integration_id": payload.integration_id,
                "message_id": message.message_id,
                "source": msgspec.to_builtins(message.source),
            },
            status_code=202,
        )

    return plugin_ingest


def make_get_conversation_handler(
    conversation_manager: ConversationManager,
):
    async def get_conversation(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            conversation = await conversation_manager.store.get_conversation(
                conversation_id
            )
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if conversation is None:
            return error_response(
                f"conversation {conversation_id!r} does not exist",
                status_code=404,
            )
        return JSONResponse(
            {
                "status": "ok",
                "data": _conversation_metadata(conversation),
            }
        )

    return get_conversation


def make_list_conversations_handler(
    conversation_manager: ConversationManager,
):
    async def list_conversations(request: Request) -> JSONResponse:
        try:
            conversations = await conversation_manager.store.list_conversations(
                actor_id=request.query_params.get("actor_id"),
            )
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return JSONResponse(
            {
                "status": "ok",
                "data": [
                    {
                        "conversation_id": item.conversation_id,
                        "title": item.title,
                        "actor_id": item.actor_id,
                        "created_at": _iso_or_none(item.created_at),
                        "updated_at": _iso_or_none(item.updated_at),
                    }
                    for item in conversations
                ],
            }
        )

    return list_conversations


def make_delete_conversation_handler(
    conversation_manager: ConversationManager,
):
    async def delete_conversation(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            deleted = await conversation_manager.delete_conversation(conversation_id)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if not deleted:
            return error_response(
                f"conversation {conversation_id!r} does not exist",
                status_code=404,
            )
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "conversation_id": conversation_id,
                    "deleted": True,
                },
            }
        )

    return delete_conversation


def make_conversation_messages_handler(
    conversation_manager: ConversationManager,
):
    async def conversation_messages(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            exists = (
                await conversation_manager.store.conversation_exists(conversation_id)
            )
            if not exists:
                return error_response(
                    f"conversation {conversation_id!r} does not exist",
                    status_code=404,
                )
            items = await conversation_manager.store.list_history_items(
                conversation_id
            )
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

        data: list[dict[str, object]] = []
        for item in items:
            if item.item_kind != "message":
                continue
            try:
                decoded = decode_prompt_item(item.item_kind, item.item_json)
            except ValueError:
                continue
            # decode_prompt_item returns Message for item_kind="message".
            if not isinstance(decoded, yuullm.Message):
                continue
            if decoded.role not in {"user", "assistant", "tool"}:
                continue
            data.append(
                _project_message_history_row(
                    item_id=item.id,
                    conversation_id=item.conversation_id,
                    message=decoded,
                    created_at=item.created_at,
                )
            )
        return JSONResponse({"status": "ok", "data": data})

    return conversation_messages


def make_model_history_handler(
    conversation_manager: ConversationManager,
):
    async def model_history(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        exists = await conversation_manager.store.conversation_exists(conversation_id)
        if not exists:
            return error_response(
                f"conversation {conversation_id!r} does not exist",
                status_code=404,
            )
        items = await conversation_manager.store.list_history_items(conversation_id)
        data: list[dict[str, object]] = []
        for item in items:
            try:
                decoded = decode_prompt_item(item.item_kind, item.item_json)
            except ValueError:
                continue
            data.append(
                {
                    "sequence": item.id,
                    "item_kind": item.item_kind,
                    "item": _decoded_item_to_builtins(decoded),
                }
            )
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "conversation_id": conversation_id,
                    "history": data,
                },
            }
        )

    return model_history


def make_send_conversation_message_handler(
    conversation_manager: ConversationManager,
):
    async def send_conversation_message(request: Request) -> JSONResponse:
        req_or_response = await _conversation_message_request_from_request(request)
        if isinstance(req_or_response, JSONResponse):
            return req_or_response
        req = req_or_response
        conversation_id = request.path_params["conversation_id"]

        binding = ConversationSendBinding(
            conversation_id=conversation_id,
            actor_id=req.actor_id,
            capability_set_id=req.capability_set_id,
            llm_backend_id=req.llm_backend_id,
            model=req.model,
        )
        try:
            _, message_id = await conversation_manager.send_message(
                conversation_id=conversation_id,
                text=_conversation_prompt_text(req.text, req.uploads),
                binding=binding,
                message_id=req.message_id.strip() or None,
            )
        except ConversationBindingConflict as exc:
            return _conversation_conflict_response(exc)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ConfigurationError as exc:
            return _configuration_error_response(exc)
        except TypeError as exc:
            return error_response(str(exc), status_code=400)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return JSONResponse(
            {
                "status": "accepted",
                "data": {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                },
            },
            status_code=202,
        )

    return send_conversation_message


def make_upload_conversation_files_handler(
    conversation_manager: ConversationManager,
):
    async def upload_conversation_files(request: Request) -> JSONResponse:
        req_or_response = await _conversation_upload_request_from_request(request)
        if isinstance(req_or_response, JSONResponse):
            return req_or_response
        binding, files = req_or_response
        conversation_id = request.path_params["conversation_id"]

        try:
            uploaded = await conversation_manager.store_uploads(
                conversation_id=conversation_id,
                files=files,
                binding=binding,
            )
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return JSONResponse(
            {
                "status": "ok",
                "data": [msgspec.to_builtins(item) for item in uploaded],
            },
            status_code=201,
        )

    return upload_conversation_files


def make_cancel_conversation_turn_handler(
    conversation_manager: ConversationManager,
):
    async def cancel_conversation_turn(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            result = await conversation_manager.cancel_turn(conversation_id)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        cancelled = bool(result.get("cancelled"))
        return JSONResponse(
            {
                "status": "cancelled" if cancelled else "idle",
                "data": {
                    "conversation_id": conversation_id,
                    "cancelled": cancelled,
                },
            },
            status_code=200,
        )

    return cancel_conversation_turn


def make_conversation_events_handler(
    conversation_manager: ConversationManager,
):
    async def conversation_events(request: Request) -> StreamingResponse:
        conversation_id = request.path_params["conversation_id"]

        async def stream():
            try:
                async for event in conversation_manager.subscribe_events(
                    conversation_id,
                ):
                    if await request.is_disconnected():
                        break
                    if isinstance(event, ConversationSSEHeartbeat):
                        # SSE comment frame: keeps the connection alive across
                        # idle periods (user typing, waiting on tool output)
                        # without surfacing anything to EventSource clients.
                        yield ": heartbeat\n\n"
                        continue
                    yield _sse_event(event.event_type, event.as_dict())
                    if event.event_type == "error":
                        break
            except LookupError:
                logger.exception(
                    "conversation SSE stream failed for %r", conversation_id
                )
                yield _sse_event(
                    "error",
                    {
                        "status": "error",
                        "error": "conversation stream terminated unexpectedly",
                    },
                )

        return StreamingResponse(stream(), media_type="text/event-stream")

    return conversation_events


# -- Projection helpers for conversation_history_items --


def _project_message_history_row(
    *,
    item_id: int,
    conversation_id: str,
    message: yuullm.Message,
    created_at: datetime | None,
) -> dict[str, object]:
    """Project one persisted ``yuullm.Message`` history item row.

    Shape mirrors the prior ``conversation_messages`` row for backwards
    compatibility with transcript consumers: ``id``, ``message_id`` (empty
    — no longer tracked at this layer), ``conversation_id``, ``role``,
    ``raw_content`` (JSON-encoded content list), ``metadata`` (empty — the
    canonical yuullm.Message no longer carries event metadata), and
    ``timestamp`` (the row id for ordering stability).
    """
    raw_content = msgspec.json.encode(
        msgspec.to_builtins(message.content)
    ).decode("utf-8")
    timestamp = int(created_at.timestamp()) if created_at is not None else item_id
    return {
        "id": item_id,
        "message_id": "",
        "conversation_id": conversation_id,
        "role": message.role,
        "raw_content": raw_content,
        "metadata": {},
        "timestamp": timestamp,
        "created_at": _iso_or_none(created_at),
    }


def _decoded_item_to_builtins(item: yuullm.PromptItem) -> object:
    """Stable JSON-safe builtins form of a decoded history item."""
    return msgspec.to_builtins(item)


def _conversation_prompt_text(
    text: str,
    uploads: list[ConversationUploadedFile],
) -> str:
    parts: list[str] = []
    body = text.strip()
    if body:
        parts.append(body)
    parts.extend(upload.prompt_line() for upload in uploads)
    return "\n\n".join(parts)
