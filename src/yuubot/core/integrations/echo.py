"""Echo integration example for testing runtime message/capability plumbing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import msgspec

from yuubot.core.capabilities import Capability, CapabilitySpec
from yuubot.core.gateway import Gateway, IntegrationIngress
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.messages import IncomingMessage, MessageSource
from yuubot.core.validation import validate_integration_config
from yuubot.resources.records import IntegrationRecord
from yuubot.resources.repository import ResourceRepository

ECHO_CAPABILITY_ID = "echo"
ECHO_INTEGRATION_PLUGIN_ID = "echo"


class EchoPayload(msgspec.Struct):
    """Typed echo payload used by runtime plumbing tests."""

    value: str = ""
    message: str = ""
    sender_id: str = ""
    message_id: str = ""


class EchoIngressPayload(msgspec.Struct, forbid_unknown_fields=False):
    """HTTP-facing message payload accepted by the echo test integration."""

    integration_id: str = ""
    message_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    kind: str = ""
    text: str = ""
    content: list[dict[str, object]] = msgspec.field(default_factory=list)
    source_path: str = ""
    timestamp: int = 0

    def to_message(self, *, default_source_path: str = "") -> IncomingMessage:
        content_items = list(self.content) if self.content else _text_content(self.text)
        fields: dict[str, object] = {
            "message_id": self.message_id or f"echo-{uuid4().hex}",
            "sender_id": self.sender_id,
            "source": MessageSource(path=self.source_path or default_source_path),
            "kind": self.kind,
            "sender_name": self.sender_name,
            "content": content_items,
        }
        if self.timestamp:
            fields["timestamp"] = self.timestamp
        return msgspec.convert(fields, type=IncomingMessage, strict=False)


class EchoIntegrationConfig(msgspec.Struct, forbid_unknown_fields=False):
    source_path: str = ""
    channel_id: str = ""


ECHO_CAPABILITY_SPEC = CapabilitySpec[EchoPayload, EchoPayload](
    id=ECHO_CAPABILITY_ID,
    name="Echo",
    description="Returns the payload unchanged.",
    input_type=EchoPayload,
    output_type=EchoPayload,
    namespace="echo",
)


@dataclass
class EchoIntegrationFactory:
    plugin_id: str = ECHO_INTEGRATION_PLUGIN_ID
    _instances: dict[str, EchoIntegration] = field(default_factory=dict)

    def capability_specs(self) -> list[CapabilitySpec[EchoPayload, EchoPayload]]:
        return [ECHO_CAPABILITY_SPEC]

    async def create(
        self,
        record: IntegrationRecord,
        repository: ResourceRepository,
        *,
        gateway: Gateway,
    ) -> "EchoIntegration":
        _ = repository
        validate_integration_config(
            record.plugin_id,
            dict(record.config),
            schema=EchoIntegrationConfig,
            context=f"integration[{record.name}]",
        )
        instance = EchoIntegration(
            ingress=gateway.open_integration(record.id),
            default_source_path=_source_path(record),
        )
        self._instances[record.id] = instance
        return instance

    def instance(self, integration_id: str) -> "EchoIntegration":
        return self._instances[integration_id]


@dataclass
class EchoIntegration:
    ingress: IntegrationIngress
    default_source_path: str = ""
    echo_calls: asyncio.Queue[EchoPayload] = field(
        default_factory=asyncio.Queue
    )
    echo_contexts: asyncio.Queue[dict[str, object]] = field(
        default_factory=asyncio.Queue
    )

    async def send_to_channel(
        self,
        *,
        message_id: str,
        sender_id: str,
        text: str = "",
        kind: str = "",
        sender_name: str = "",
        content: list[dict[str, object]] | None = None,
    ) -> None:
        await self.emit_message(
            message_id=message_id,
            sender_id=sender_id,
            kind=kind,
            sender_name=sender_name,
            content_items=content or _text_content(text),
        )

    async def emit_message(
        self,
        *,
        message_id: str,
        sender_id: str,
        kind: str = "",
        sender_name: str = "",
        content_items: list[dict[str, object]] | None = None,
        source_path: str = "",
    ) -> IncomingMessage:
        message = IncomingMessage(
            message_id=message_id,
            sender_id=sender_id,
            source=MessageSource(path=source_path or self.default_source_path),
            kind=kind,
            sender_name=sender_name,
            content=content_items or [],
        )
        await self.ingress.emit(message)
        return message

    async def emit_payload(self, payload: EchoIngressPayload) -> IncomingMessage:
        message = payload.to_message(default_source_path=self.default_source_path)
        await self.ingress.emit(message)
        return message

    def capabilities(self) -> list[Capability[EchoPayload, EchoPayload]]:
        return [
            Capability(
                id=ECHO_CAPABILITY_ID,
                name="Echo",
                description="Returns the payload unchanged.",
                input_type=EchoPayload,
                output_type=EchoPayload,
                namespace="echo",
                invoke=self.invoke_echo,
            ),
        ]

    async def invoke_echo(
        self,
        payload: EchoPayload,
        context: InvocationContext,
    ) -> EchoPayload:
        await self.echo_calls.put(payload)
        await self.echo_contexts.put(
            {
                "actor_id": context.actor_id,
                "source_id": context.source_id,
                "source_path": context.source_path,
                "raw": context.raw,
            }
        )
        return payload

    async def close(self) -> None:
        pass

    async def next_echo_call(self) -> EchoPayload:
        return await asyncio.wait_for(self.echo_calls.get(), timeout=1.0)

    async def next_echo_context(self) -> dict[str, object]:
        return await asyncio.wait_for(self.echo_contexts.get(), timeout=1.0)


def _text_content(text: str) -> list[dict[str, object]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _source_path(record: IntegrationRecord) -> str:
    value = record.config.get("source_path", record.config.get("channel_id", ""))
    if not isinstance(value, str):
        raise TypeError("echo integration source_path must be a string")
    return value
