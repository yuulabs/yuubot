"""Gateway contracts and context-based routing.

Channel adapters translate platform-specific events into IncomingMessage.
Gateway turns those into Context-bound InboundMessage objects and routes replies
back through the adapter registered for Context.channel.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import msgspec
from loguru import logger

from yuubot.core.models import Context, Segment, TextSegment
from yuubot.core.types import InboundMessage, Sender

DEFAULT_KIND = "other"


class ContextRef(msgspec.Struct, frozen=True):
    """Stable conversation identity produced by a channel adapter."""

    channel: str
    key: str
    kind: str = DEFAULT_KIND
    label: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class IncomingMessage(msgspec.Struct):
    """Channel-neutral inbound message emitted by ChannelAdapter.start()."""

    context: ContextRef
    message_id: str
    sender_id: str
    sender_name: str = ""
    segments: list[Segment] = msgspec.field(default_factory=list)
    text: str = ""
    timestamp: int = 0
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class OutboundMessage(msgspec.Struct):
    """Payload sent back through a ChannelAdapter."""

    text: str = ""
    segments: list[Segment] = msgspec.field(default_factory=list)
    reply_to: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


class ChannelAdapter(Protocol):
    """Minimal adapter interface for one platform/channel."""

    channel: str

    async def start(
        self,
        emit: Callable[[IncomingMessage], Awaitable[None]],
    ) -> None:
        """Connect to the platform and emit IncomingMessage for each message."""
        ...

    async def stop(self) -> None: ...

    async def send(self, ctx: Context, message: OutboundMessage) -> None:
        """Send *message* to the platform conversation represented by *ctx*."""
        ...


class Gateway:
    """Small registry that bridges ChannelAdapter objects to the dispatcher."""

    def __init__(self, dispatcher: Any | None = None) -> None:
        self.dispatcher = dispatcher
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        if adapter.channel in self._adapters:
            raise ValueError(f"duplicate channel adapter: {adapter.channel}")
        self._adapters[adapter.channel] = adapter

    async def ingest(self, incoming: IncomingMessage) -> None:
        if self.dispatcher is None:
            raise RuntimeError("Gateway.ingest requires a dispatcher")
        ctx = await get_or_create_context(incoming.context)
        inbound = inbound_from_incoming(incoming, ctx.id)
        await self.dispatcher.dispatch_message(inbound)

    async def send(self, ctx_id: int, message: OutboundMessage) -> None:
        ctx = await Context.get(id=ctx_id)
        adapter = self._adapters.get(ctx.channel)
        if adapter is None:
            raise KeyError(f"no adapter registered for channel: {ctx.channel}")
        await adapter.send(ctx, message)


async def get_or_create_context(ref: ContextRef) -> Context:
    """Return the Context for a channel conversation, creating it if necessary."""
    ctx, created = await Context.get_or_create(
        channel=ref.channel,
        key=ref.key,
        defaults={
            "kind": ref.kind or DEFAULT_KIND,
            "label": ref.label,
            "metadata": dict(ref.metadata),
            "last_message_at": datetime.now(UTC),
        },
    )
    if not created:
        updates: dict[str, Any] = {"last_message_at": datetime.now(UTC)}
        if ref.kind and ctx.kind != ref.kind:
            updates["kind"] = ref.kind
        if ref.label and ctx.label != ref.label:
            updates["label"] = ref.label
        if ref.metadata and ctx.metadata != ref.metadata:
            updates["metadata"] = dict(ref.metadata)
        if len(updates) > 1:
            await Context.filter(id=ctx.id).update(**updates)
            for field, value in updates.items():
                setattr(ctx, field, value)
    return ctx


def inbound_from_incoming(incoming: IncomingMessage, ctx_id: int) -> InboundMessage:
    """Convert the adapter-facing message into the current dispatcher type."""
    segments: list[Segment]
    if incoming.segments:
        segments = list(incoming.segments)
    elif incoming.text:
        segments = [TextSegment(text=incoming.text)]
    else:
        segments = []
    return InboundMessage(
        message_id=incoming.message_id,
        ctx_id=ctx_id,
        chat_type=_legacy_chat_type(incoming.context.kind),
        sender=Sender(user_id=0, nickname=incoming.sender_name),
        segments=segments,
        timestamp=incoming.timestamp,
        raw_message=incoming.text,
        metadata={
            **incoming.metadata,
            "sender_id": incoming.sender_id,
            "channel": incoming.context.channel,
            "context_key": incoming.context.key,
        },
    )


def _legacy_chat_type(kind: str) -> Literal["private", "group"]:
    return "private" if kind == "private" else "group"


class RoutingRule(msgspec.Struct, frozen=True):
    """A single routing rule: if *match* fits the Context, use *actor*."""

    match: dict[str, Any]
    actor: str


class RoutingEngine:
    """Select an actor name based on Context attributes.

    Resolution order:
    1. Custom rules, first match wins.
    2. Default actor for ctx.kind.
    3. The ``other`` default.
    """

    def __init__(
        self,
        rules: list[RoutingRule] | None = None,
        defaults: dict[str, str] | None = None,
        default_group: str = "yuu",
        default_private: str = "shiori",
        default_other: str = "yuu",
    ) -> None:
        self._rules = rules or []
        self._defaults = {
            "group": default_group,
            "private": default_private,
            "other": default_other,
            **(defaults or {}),
        }

    async def select_actor(self, message: InboundMessage) -> str:
        ctx = await Context.get(id=message.ctx_id)
        for rule in self._rules:
            if self._match(rule.match, ctx, message):
                return rule.actor
        return self._defaults.get(_ctx_kind(ctx), self._defaults["other"])

    def _match(self, pattern: dict[str, Any], ctx: Context, message: InboundMessage) -> bool:
        for key, expected in pattern.items():
            actual = self._value_for_key(key, ctx, message)
            if not self._compare(actual, expected):
                return False
        return True

    def _value_for_key(self, key: str, ctx: Context, message: InboundMessage) -> Any:
        if key.startswith("metadata."):
            return _dict_get(ctx.metadata or {}, key.removeprefix("metadata."))
        if key.startswith("sender."):
            return self._get_message_field(message, key)
        if key.startswith("message."):
            return self._get_message_field(message, key.removeprefix("message."))
        if key == "kind":
            return _ctx_kind(ctx)
        if key in {"channel", "key", "label"}:
            return getattr(ctx, key, None)
        # Compatibility for older rules that matched boolean context flags.
        if key in {"is_group", "is_private"}:
            return getattr(ctx, key, None)
        return None

    @staticmethod
    def _get_message_field(message: InboundMessage, field: str) -> Any:
        parts = field.split(".")
        obj: Any = message
        for part in parts:
            if obj is None:
                return None
            obj = getattr(obj, part, None)
        return obj

    @staticmethod
    def _compare(actual: Any, expected: Any) -> bool:
        if isinstance(expected, dict):
            if "$exists" in expected:
                return (actual is not None) == expected["$exists"]
            if "$in" in expected:
                return actual in expected["$in"]
            logger.warning("Unsupported routing operator in {}", expected)
            return False
        return actual == expected


def _ctx_kind(ctx: Context) -> str:
    if ctx.kind and ctx.kind != DEFAULT_KIND:
        return ctx.kind
    if ctx.type in {"group", "private"}:
        return ctx.type
    if ctx.is_private:
        return "private"
    if ctx.is_group:
        return "group"
    return DEFAULT_KIND


def _dict_get(data: dict[str, Any], dotted_key: str) -> Any:
    value: Any = data
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def build_routing_engine(routing_cfg: dict[str, Any] | None) -> RoutingEngine:
    """Build a RoutingEngine from the 'routing' section of config.yaml."""
    if not routing_cfg:
        return RoutingEngine()

    defaults = {
        str(key): str(value)
        for key, value in dict(routing_cfg.get("defaults", {})).items()
        if value is not None
    }
    rules_raw = routing_cfg.get("rules", [])

    rules: list[RoutingRule] = []
    for rule in rules_raw:
        if isinstance(rule, dict) and "match" in rule and "actor" in rule:
            rules.append(RoutingRule(match=rule["match"], actor=str(rule["actor"])))
        else:
            logger.warning("Skipping malformed routing rule: {}", rule)

    return RoutingEngine(rules=rules, defaults=defaults)
