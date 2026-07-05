"""Inbound envelope types, secret resolution, and wakeup delivery helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol

import msgspec
from attrs import define
from fastapi import Request

from ..domain.messages import ActorMessage
from .wakeup import WakeupDelivery, WakeupPayload, WakeupTarget


class InboundEnvelope(msgspec.Struct, frozen=True):
    text: str
    conversation_id: str | None = None
    route: str | None = None
    source: dict[str, object] = msgspec.field(default_factory=dict)

class InboundBadRequestError(ValueError):
    pass


class InboundUnauthorizedError(Exception):
    pass


class MailboxUnavailableError(RuntimeError):
    pass


class SecretResolver(Protocol):
    def resolve(self, ref: str) -> str | None: ...


class RouteGateway(Protocol):
    def resolve(self, route: str) -> str | None: ...


@define
class EnvSecretResolver:
    def resolve(self, ref: str) -> str | None:
        return os.environ.get(ref)


class IntegrationInboundAdapter(Protocol):
    async def validate_webhook(
        self,
        request: Request,
        *,
        secrets: SecretResolver,
    ) -> InboundEnvelope: ...


class JsonInboundAdapter:
    """Default v1 adapter: JSON body maps directly to InboundEnvelope."""

    async def validate_webhook(
        self,
        request: Request,
        *,
        secrets: SecretResolver,
    ) -> InboundEnvelope:
        del secrets
        try:
            envelope = msgspec.json.decode(await request.body(), type=InboundEnvelope)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            raise InboundBadRequestError(str(exc)) from exc
        if not envelope.text:
            raise InboundBadRequestError("text is required")
        if envelope.route is None or not envelope.route:
            raise InboundBadRequestError("route is required")
        return envelope


DEFAULT_INBOUND_ADAPTER = JsonInboundAdapter()


async def deliver_app_webhook(
    *,
    integration_type: str,
    envelope: InboundEnvelope,
    gateway: RouteGateway,
    wakeup: WakeupDelivery,
    emit: Callable[..., None],
) -> dict[str, object]:
    route = envelope.route
    if route is None:
        raise InboundBadRequestError("route is required")

    emit("incoming.message", route=route, text=envelope.text, source=envelope.source)
    actor_id = gateway.resolve(route)
    delivered = False
    if actor_id is not None:
        await wakeup.deliver(
            WakeupTarget(
                kind="app_webhook",
                actor_id=actor_id,
                conversation_id=envelope.conversation_id,
            ),
            WakeupPayload(text=envelope.text, source=envelope.source),
        )
        delivered = True
    emit(
        "gateway.dispatch",
        route=route,
        actor_id=actor_id,
        delivered=delivered,
        conversation_id=envelope.conversation_id,
    )
    result: dict[str, object] = {
        "integration_type": integration_type,
        "delivered": delivered,
        "conversation_id": envelope.conversation_id,
    }
    if actor_id is not None:
        result["actor_id"] = actor_id
    return result


async def deliver_actor_inbound(
    *,
    actor_id: str,
    body: ActorMessage,
    wakeup: WakeupDelivery,
    actor_running: bool,
) -> dict[str, object]:
    if not actor_running:
        raise MailboxUnavailableError(f"actor mailbox is not available: {actor_id}")
    await wakeup.deliver(
        WakeupTarget(
            kind="actor_inbound",
            actor_id=actor_id,
            conversation_id=body.conversation_id,
        ),
        WakeupPayload(text=body.text, source=body.source),
    )
    return {
        "actor_id": actor_id,
        "conversation_id": body.conversation_id,
        "delivered": True,
    }
