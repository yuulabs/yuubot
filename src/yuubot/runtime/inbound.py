"""Inbound envelope types, secret resolution, and wakeup delivery helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol

import msgspec
from attrs import define
from fastapi import Request

from ..domain.messages import ActorMessage
from .event_payloads import EmitFn, GatewayDispatchPayload, IncomingMessagePayload
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
        secrets: SecretResolver,
        require_signature: bool = False,
    ) -> InboundEnvelope: ...


class JsonInboundAdapter:
    """Default v1 adapter: JSON body maps directly to InboundEnvelope."""

    def __init__(self, secret_ref: str = "") -> None:
        self.secret_ref = secret_ref

    async def validate_webhook(
        self,
        request: Request,
        secrets: SecretResolver,
        require_signature: bool = False,
    ) -> InboundEnvelope:
        body = await request.body()
        if require_signature:
            self._validate_signature(request, body, secrets)
        try:
            envelope = msgspec.json.decode(body, type=InboundEnvelope)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            raise InboundBadRequestError(str(exc)) from exc
        if not envelope.text:
            raise InboundBadRequestError("text is required")
        if envelope.route is None or not envelope.route:
            raise InboundBadRequestError("route is required")
        return envelope

    def _validate_signature(self, request: Request, body: bytes, secrets: SecretResolver) -> None:
        if not self.secret_ref:
            raise InboundUnauthorizedError("webhook signature secret is not configured")
        secret = secrets.resolve(self.secret_ref)
        if not secret:
            raise InboundUnauthorizedError("webhook signature secret is not configured")
        signature = request.headers.get("x-yuubot-webhook-signature", "").strip()
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if signature.startswith("sha256="):
            signature = signature.removeprefix("sha256=")
        if not hmac.compare_digest(signature, expected):
            raise InboundUnauthorizedError("invalid webhook signature")


DEFAULT_INBOUND_ADAPTER = JsonInboundAdapter()


async def deliver_app_webhook(
    integration_type: str,
    envelope: InboundEnvelope,
    gateway: RouteGateway,
    wakeup: WakeupDelivery,
    emit: EmitFn,
) -> dict[str, object]:
    route = envelope.route
    if route is None:
        raise InboundBadRequestError("route is required")

    emit(IncomingMessagePayload(route, envelope.text, envelope.source))
    actor_id = gateway.resolve(route)
    delivered = False
    if actor_id is not None:
        await wakeup.deliver(
            WakeupTarget(
                "app_webhook",
                actor_id,
                envelope.conversation_id,
            ),
            WakeupPayload(envelope.text, envelope.source),
        )
        delivered = True
    emit(
        GatewayDispatchPayload(
            route,
            actor_id,
            delivered,
            envelope.conversation_id,
        )
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
    actor_id: str,
    body: ActorMessage,
    wakeup: WakeupDelivery,
    actor_running: bool,
) -> dict[str, object]:
    if not actor_running:
        raise MailboxUnavailableError(f"actor mailbox is not available: {actor_id}")
    await wakeup.deliver(
        WakeupTarget(
            "actor_inbound",
            actor_id,
            body.conversation_id,
        ),
        WakeupPayload(body.text, body.source),
    )
    return {
        "actor_id": actor_id,
        "conversation_id": body.conversation_id,
        "delivered": True,
    }
