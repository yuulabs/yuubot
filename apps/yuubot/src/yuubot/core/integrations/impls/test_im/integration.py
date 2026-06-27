"""Test IM integration — multi-channel IM simulator.

Channels are configured via ``TestImConfig.channels``. Each channel has an
outbound queue that test assertions read via ``next_outbound()``. The
integration registers **no capabilities** — it is purely an ingress/egress
testing tool for the new ``path``-based response model.

Usage in tests::

    instance.send_to_channel("group-1", sender_id="user-1", text="Hello")
    outbound = await instance.next_outbound("group-1")
    assert outbound == {"path": "group-1", "msg": "Hello back"}
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import msgspec

from typing import TYPE_CHECKING

from yuubot.core.gateway import Gateway, IntegrationIngress
from yuubot.core.integrations.contracts import (
    IntegrationInstance,
    IntegrationSdkSpec,
    IntegrationStorage,
    ReactionKind,
)
from yuubot.core.messages import IncomingMessage, MessageSource
from yuubot.resources.records import IntegrationRecord

if TYPE_CHECKING:
    from yuubot.core.integrations.core import IntegrationCore

TEST_IM_INTEGRATION_NAME = "test_im"
TEST_IM_TIMEOUT_S = 5.0


@dataclass
class TestImChannel:
    """Describes a single IM channel."""

    id: str
    name: str = ""
    source_path: str = ""


class TestImConfig(msgspec.Struct, forbid_unknown_fields=False):
    """Configuration for a test_im integration instance.

    Each entry in ``channels`` creates a named channel with its own
    outbound queue and source identity.
    """

    channels: list[TestImConfigChannel] = msgspec.field(default_factory=list)


class TestImConfigChannel(msgspec.Struct):
    """A single channel entry within TestImConfig."""

    id: str
    name: str = ""
    source_path: str = ""


@dataclass
class TestImIntegrationState:
    """Per-channel state for the test IM integration."""

    channel: TestImChannel
    outbound: asyncio.Queue[dict[str, object]] = field(default_factory=asyncio.Queue)


@dataclass
class TestImIntegration(IntegrationInstance):
    """Multi-channel IM simulator for testing path-based responses."""

    ingress: IntegrationIngress
    channels: dict[str, TestImIntegrationState] = field(default_factory=dict)
    _config: TestImConfig = field(default_factory=TestImConfig)

    def capabilities(self) -> list:
        """No capabilities — this is purely an ingress/egress test tool."""
        return []

    async def send_to_channel(
        self,
        channel_id: str,
        *,
        sender_id: str = "",
        text: str = "",
        kind: str = "",
        sender_name: str = "",
        content: list[dict[str, object]] | None = None,
        message_id: str = "",
    ) -> IncomingMessage:
        """Simulate an inbound message on *channel_id*.

        The emitted ``IncomingMessage`` carries ``source.id`` set to this
        integration's id (set by the ingress) and ``source.path`` matching
        the channel's configured ``source_path``.
        """
        state = self.channels.get(channel_id)
        if state is None:
            raise LookupError(f"test_im channel {channel_id!r} is not configured")

        import uuid

        message = IncomingMessage(
            message_id=message_id or f"test-im-{uuid.uuid4().hex}",
            sender_id=sender_id,
            source=MessageSource(path=state.channel.source_path),
            kind=kind,
            sender_name=sender_name,
            content=content or _text_content(text),
        )
        await self.ingress.emit(message)
        return message

    async def response(
        self,
        target_msg_id: str,
        *,
        path: str = "",
        msg: str = "",
        react: ReactionKind | None = None,
    ) -> None:
        """Store an outbound message in the matching channel's queue.

        If ``path`` matches a configured channel id, the message is queued
        there. Otherwise it is silently dropped (the integration does not
        know about that channel).
        """
        _ = target_msg_id
        _ = react
        state = self.channels.get(path)
        if state is None:
            return
        await state.outbound.put({"path": path, "msg": msg, "target_msg_id": target_msg_id})

    async def next_outbound(self, channel_id: str, *, timeout: float = TEST_IM_TIMEOUT_S) -> dict[str, object]:
        """Read the next outbound message from *channel_id* (test assertion)."""
        state = self.channels.get(channel_id)
        if state is None:
            raise LookupError(f"test_im channel {channel_id!r} is not configured")
        return await asyncio.wait_for(state.outbound.get(), timeout=timeout)

    async def close(self) -> None:
        pass


@dataclass
class TestImIntegrationFactory:
    """Factory for TestImIntegration instances."""

    name: str = TEST_IM_INTEGRATION_NAME
    description: str = "Multi-channel IM simulator for testing path-based responses."
    config_schema: type[msgspec.Struct] = TestImConfig
    _instances: dict[str, TestImIntegration] = field(default_factory=dict)

    def capability_specs(self) -> list:
        return []

    @property
    def sdk_spec(self) -> IntegrationSdkSpec:
        # test_im is an inbound-only IM ingress integration: it registers no
        # callable facade module and contributes nothing to the system prompt's
        # # Integration SDKs section (§2.7.1).
        return IntegrationSdkSpec()

    @property
    def source_path_convention(self) -> str:
        return (
            "Each channel defines its own source_path in the config. "
            "Typical shape: ``channels/<channel_id>``."
        )

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> TestImIntegration:
        _ = storage
        config = record.typed_config(TestImConfig)
        channels: dict[str, TestImIntegrationState] = {}
        for ch in config.channels:
            channel = TestImChannel(id=ch.id, name=ch.name, source_path=ch.source_path)
            channels[ch.id] = TestImIntegrationState(channel=channel)
        instance = TestImIntegration(
            ingress=gateway.open_integration(record.id),
            channels=channels,
            _config=config,
        )
        self._instances[record.id] = instance
        return instance

    def routes(self, integrations: IntegrationCore) -> list:
        from yuubot.core.integrations.impls.test_im.routes import test_im_routes

        return test_im_routes(integrations)

    def instance(self, integration_id: str) -> TestImIntegration:
        return self._instances[integration_id]


def _text_content(text: str) -> list[dict[str, object]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]
