"""Daemon HTTP route handlers extracted from app.py.

Each handler is returned by a factory function that receives its
dependencies explicitly — no closure capture, no hidden state.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import cast

import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from yuubot.bootstrap.config import ServerConfig
from yuubot.core.actors import ActorManager
from yuubot.core.conversations import ConversationManager
from yuubot.core.integrations import IntegrationCore
from yuubot.core.validation import ConfigurationError
from yuubot.resources.events import ResourceChanged
from yuubot.resources.registry import EventDrivenRefreshDispatcher
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM
from yuubot.runtime.http_utils import error_response
from yuubot.runtime.plugin_manager import (
    ExternalPluginInboundMessage,
    ExternalPluginIntegration,
    ExternalPluginManager,
)
from yuubot.runtime.process import ServiceHost, TraceService

logger = logging.getLogger(__name__)

# -- Shared request structs (was in app.py) --


class ConversationRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for conversation creation requests."""

    conversation_id: str = ""
    actor_id: str = ""
    character_id: str = ""
    capability_set_id: str = ""
    llm_backend_id: str = ""
    model: str = ""
    title: str = ""
    reply_address: str = ""
    metadata: dict[str, object] = msgspec.field(default_factory=dict)


class ConversationMessageRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for conversation message requests."""

    text: str = ""
    content: list[dict[str, object]] = msgspec.field(default_factory=list)
    message_id: str = ""


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


async def _conversation_payload_from_request(
    request: Request,
) -> ConversationRequest | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return error_response("request body must be a JSON object", status_code=400)

    try:
        req = msgspec.convert(payload, type=ConversationRequest, strict=False)
    except msgspec.ValidationError, msgspec.DecodeError:
        return error_response("invalid request body", status_code=400)

    if not req.actor_id.strip() and not (
        req.character_id.strip()
        and req.capability_set_id.strip()
        and req.llm_backend_id.strip()
    ):
        return error_response(
            "actor_id or character_id/capability_set_id/llm_backend_id must be provided",
            status_code=400,
        )
    return req


def _content_items_from_request(
    req: ConversationMessageRequest,
) -> list[dict[str, object]] | JSONResponse:
    if req.text.strip():
        return [{"type": "text", "text": req.text.strip()}]

    if not req.content:
        return error_response(
            "text or content must be provided",
            status_code=400,
        )

    result: list[dict[str, object]] = []
    for item in req.content:
        result.append({str(key): value for key, value in item.items()})
    return result


async def _conversation_message_payload_from_request(
    request: Request,
) -> dict[str, object] | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return error_response("request body must be a JSON object", status_code=400)

    try:
        req = msgspec.convert(payload, type=ConversationMessageRequest, strict=False)
    except msgspec.ValidationError, msgspec.DecodeError:
        return error_response("invalid request body", status_code=400)

    content = _content_items_from_request(req)
    if isinstance(content, JSONResponse):
        return content

    message_id = req.message_id.strip() or None

    return {
        "content": content,
        "message_id": message_id,
    }


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


def make_create_conversation_handler(
    conversation_manager: ConversationManager,
):
    async def create_conversation(request: Request) -> JSONResponse:
        payload_or_response = await _conversation_payload_from_request(request)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response
        req = payload_or_response
        conversation_id = req.conversation_id.strip() or uuid.uuid4().hex
        try:
            if req.actor_id.strip():
                conversation = await conversation_manager.create_from_actor_defaults(
                    conversation_id=conversation_id,
                    actor_id=req.actor_id.strip(),
                    title=req.title,
                    reply_address=req.reply_address,
                    metadata=req.metadata,
                )
            else:
                conversation = await conversation_manager.create_from_refs(
                    conversation_id=conversation_id,
                    character_id=req.character_id.strip(),
                    capability_set_id=req.capability_set_id.strip(),
                    llm_backend_id=req.llm_backend_id.strip(),
                    model=req.model.strip(),
                    title=req.title,
                    reply_address=req.reply_address,
                    metadata=req.metadata,
                )
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except TypeError as exc:
            return error_response(str(exc), status_code=400)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "conversation_id": conversation.conversation_id,
                    "actor_id": conversation.actor_id,
                    "character_id": conversation.character.id,
                    "capability_set_id": conversation.capability_set.id,
                    "llm_backend_id": conversation.llm_backend.id,
                    "model": conversation.model,
                    "created_at": _iso_or_none(conversation.created_at),
                    "updated_at": _iso_or_none(conversation.updated_at),
                },
            },
            status_code=201,
        )

    return create_conversation


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
                        "actor_id": item.actor_id,
                        "created_at": _iso_or_none(item.created_at),
                        "updated_at": _iso_or_none(item.updated_at),
                    }
                    for item in conversations
                ],
            }
        )

    return list_conversations


def make_ensure_conversation_agent_handler(
    conversation_manager: ConversationManager,
):
    async def ensure_conversation_agent(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            data = await conversation_manager.ensure_agent(conversation_id)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ConfigurationError as exc:
            return _configuration_error_response(exc)
        except TypeError as exc:
            return error_response(str(exc), status_code=400)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return JSONResponse({"status": "ok", "data": data})

    return ensure_conversation_agent


def make_conversation_messages_handler(
    conversation_manager: ConversationManager,
):
    async def conversation_messages(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            messages = await conversation_manager.store.list_messages(conversation_id)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return JSONResponse(
            {
                "status": "ok",
                "data": [
                    {
                        "id": message.id,
                        "message_id": message.message_id,
                        "conversation_id": message.conversation_id,
                        "role": message.role,
                        "raw_content": message.raw_content,
                        "metadata": message.metadata,
                        "timestamp": message.timestamp,
                    }
                    for message in messages
                ],
            }
        )

    return conversation_messages


def make_send_conversation_message_handler(
    conversation_manager: ConversationManager,
):
    async def send_conversation_message(request: Request) -> JSONResponse:
        payload_or_response = await _conversation_message_payload_from_request(request)
        if isinstance(payload_or_response, JSONResponse):
            return payload_or_response
        payload = payload_or_response
        conversation_id = request.path_params["conversation_id"]
        try:
            record = await conversation_manager.send_message(
                conversation_id=conversation_id,
                content=cast(list[dict[str, object]], payload["content"]),
                message_id=cast(str | None, payload.get("message_id")),
            )
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
                    "conversation_id": record.conversation_id,
                    "message_id": record.message_id,
                },
            },
            status_code=202,
        )

    return send_conversation_message


def make_conversation_events_handler(
    conversation_manager: ConversationManager,
):
    async def conversation_events(request: Request) -> StreamingResponse:
        conversation_id = request.path_params["conversation_id"]

        async def stream():
            try:
                async for event in conversation_manager.subscribe_events(
                    conversation_id
                ):
                    if await request.is_disconnected():
                        break
                    yield _sse_event(event.event_type, event.as_dict())
                    if event.event_type in ("turn_completed", "error"):
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
