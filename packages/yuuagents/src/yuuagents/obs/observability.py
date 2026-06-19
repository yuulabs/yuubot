from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol, cast
from uuid import NAMESPACE_DNS, UUID, uuid5

import yuullm
import yuutrace
from attrs import define, field
from opentelemetry import trace
from opentelemetry.util.types import AttributeValue
from yuutrace.otel import (
    ATTR_ENTITY_BLOCKS,
    ATTR_ENTITY_CHUNK_INDEX,
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PARENT_ID,
    ATTR_ENTITY_STATUS,
    ATTR_ENTITY_TOOL_CALL_ID,
    ATTR_ENTITY_TYPE,
    ATTR_TOOL_CALL_ID,
    ATTR_TOOL_NAME,
    ATTR_TOOL_USAGE_QUANTITY,
    ATTR_TOOL_USAGE_UNIT,
    EVENT_TOOL_USAGE,
    OtelAttributes,
)

from yuuagents.obs.entitylog import (
    CommandBlock,
    ContentBlock,
    EntityLogBlock,
    ProcessBlock,
    blocks_to_builtins,
    blocks_to_json,
)
from yuuagents.core.eventbus import RuntimeEvent, ScopeResult
from yuuagents.types.values import EventValue

_TRACER_NAME = "yuutrace"

TOOL_COST_UNITS = {"usd", "USD"}


class TraceContextProvider(Protocol):
    """Host-provided trace identity and attributes for yuuagents events."""

    def conversation_id(self, event: RuntimeEvent) -> UUID | str | None: ...

    def agent_name(self, event: RuntimeEvent) -> str: ...

    def model(self, event: RuntimeEvent) -> str: ...

    def tags(self, event: RuntimeEvent) -> list[str] | None: ...

    def event_attributes(self, event: RuntimeEvent) -> Mapping[str, AttributeValue]: ...


@define
class DefaultTraceContextProvider:
    """Default yuuagents-only trace context."""

    def conversation_id(self, event: RuntimeEvent) -> UUID | str | None:
        if not event.agent_id:
            return None
        return uuid5(NAMESPACE_DNS, event.agent_id)

    def agent_name(self, event: RuntimeEvent) -> str:
        return event.agent_name

    def model(self, event: RuntimeEvent) -> str:
        value = event.data.get("model")
        return value if isinstance(value, str) else ""

    def tags(self, event: RuntimeEvent) -> list[str] | None:
        return None

    def event_attributes(self, event: RuntimeEvent) -> Mapping[str, AttributeValue]:
        return {}


@define
class AgentTraceContext:
    conversation_id: UUID
    agent_name: str
    model: str
    tags: list[str] | None = None


@define
class YuuTraceObserver:
    """Bridge yuuagents runtime events into yuutrace spans and events."""

    context_provider: TraceContextProvider = field(factory=DefaultTraceContextProvider)
    _contexts: dict[str, AgentTraceContext] = field(
        factory=dict, init=False, repr=False
    )
    _conversations: dict[str, yuutrace.ConversationContext] = field(
        factory=dict, init=False, repr=False
    )
    _turns: dict[str, yuutrace.TurnContext] = field(
        factory=dict, init=False, repr=False
    )
    _entities: dict[str, yuutrace.EntityContext] = field(
        factory=dict, init=False, repr=False
    )

    def on_scope(self, event: RuntimeEvent) -> ScopeResult:
        if event.name == "agent.turn":
            return self._turn_scope(event)
        return None

    async def on_event(self, event: RuntimeEvent) -> None:
        if event.name == "agent.started":
            self._on_agent_started(event)
            return
        if event.name == "llm.finished":
            self._on_llm_finished(event)
            return
        if event.name == "runtime.usage_reported":
            self._on_usage_reported(event)
            return
        if event.name == "output.entity":
            self._record_entity(event)
            return
        if event.name == "output.chunk":
            self._record_entity_chunk(event)
            return
        if event.name == "output.entity_end":
            self._record_entity_end(event)
            return
        if event.name in {
            "agent.turn.error",
            "llm.started",
            "runtime.task_created",
            "runtime.task_completed",
            "runtime.task_error",
            "runtime.task_cancelled",
            "runtime.task_detached",
            "runtime.task_killed",
            "budget.exceeded",
            "runtime.task_failed",
            "runtime.task_timed_out",
            "tool.result_appended",
        }:
            self._record_runtime_event(event)

    def _on_agent_started(self, event: RuntimeEvent) -> None:
        if not event.agent_id or event.agent_id in self._contexts:
            return
        conversation_id = self._conversation_id(event)
        if conversation_id is None:
            return
        ctx = AgentTraceContext(
            conversation_id=conversation_id,
            agent_name=self.context_provider.agent_name(event),
            model=self.context_provider.model(event),
            tags=self.context_provider.tags(event),
        )
        self._contexts[event.agent_id] = ctx
        self._conversations[event.agent_id] = yuutrace.start_conversation(
            id=ctx.conversation_id,
            agent=ctx.agent_name,
            model=ctx.model,
            tags=ctx.tags,
        )
        self._record_initial_history(event)

    @contextmanager
    def _turn_scope(self, event: RuntimeEvent) -> Iterator[yuutrace.TurnContext | None]:
        conv = self._conversations.get(event.agent_id)
        if conv is None:
            yield None
            return
        with conv.turn("assistant") as turn:
            self._turns[event.agent_id] = turn
            try:
                yield turn
            finally:
                self._turns.pop(event.agent_id, None)

    def _on_llm_finished(self, event: RuntimeEvent) -> None:
        turn = self._turns.get(event.agent_id)
        usage_raw = event.data.get("usage")
        if usage_raw is None:
            self._record_runtime_event(event)
            return
        message = event.data.get("message")
        if turn is not None and message is not None:
            content = getattr(message, "content", None)
            if content is not None:
                turn.add(*content)
        cost = cast("yuutrace.LlmCost | None", event.data.get("cost"))
        try:
            if turn is not None:
                turn.usage(cast(yuutrace.LlmUsage, usage_raw), cost=cost)
            else:
                yuutrace.record_llm_usage(cast(yuutrace.LlmUsage, usage_raw), cost=cost)
        except yuutrace.NoActiveSpanError:
            return

    def _on_usage_reported(self, event: RuntimeEvent) -> None:
        service = event.data.get("service")
        unit = event.data.get("unit")
        amount_raw = event.data.get("amount", 0.0)
        if not isinstance(service, str) or not isinstance(unit, str):
            return
        if not isinstance(amount_raw, int | float):
            return
        amount = float(amount_raw)
        task_id = _string_or_none(event.data.get("task_id"))
        attrs = self._event_attributes(event)
        attrs.update(
            {
                ATTR_TOOL_NAME: service,
                ATTR_TOOL_USAGE_UNIT: unit,
                ATTR_TOOL_USAGE_QUANTITY: amount,
            }
        )
        if task_id:
            attrs[ATTR_TOOL_CALL_ID] = task_id
        try:
            yuutrace.add_event(EVENT_TOOL_USAGE, attrs)
            if unit in TOOL_COST_UNITS:
                yuutrace.record_cost(
                    category="tool",
                    currency="USD",
                    amount=amount,
                    source="runtime.usage_reported",
                    tool_name=service,
                    tool_call_id=task_id,
                )
        except yuutrace.NoActiveSpanError:
            return

    def _record_runtime_event(self, event: RuntimeEvent) -> None:
        attrs = self._event_attributes(event)
        for key, value in event.data.items():
            attr = _attribute_value(value)
            if attr is not None:
                attrs.setdefault(f"yuu.event.{key}", attr)
        try:
            yuutrace.add_event(event.name, attrs)
        except yuutrace.NoActiveSpanError:
            return

    def _record_entity(self, event: RuntimeEvent) -> None:
        entity_id = _string_or_none(event.data.get("entity_id"))
        entity_type = _string_or_none(event.data.get("entity_type"))
        if entity_id and entity_type:
            conv = self._conversation_for_entity_event(event)
            if conv is not None:
                entity = conv.start_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    parent_id=_string_or_none(event.data.get("parent_id")) or "",
                    tool_call_id=_string_or_none(event.data.get("tool_call_id")),
                )
                self._entities[entity_id] = entity
                return
        attrs = self._entity_attrs(event)
        self._record_immediate_span("entity", attrs)

    def _record_entity_chunk(self, event: RuntimeEvent) -> None:
        entity_id = _string_or_none(event.data.get("entity_id"))
        entity = self._entities.get(entity_id or "")
        blocks = event.data.get("blocks")
        if entity is not None and isinstance(blocks, list):
            entity.flush(blocks_to_builtins(_entity_blocks(blocks)))
            return
        attrs = self._entity_attrs(event)
        chunk_index = event.data.get("chunk_index")
        if isinstance(chunk_index, int):
            attrs[ATTR_ENTITY_CHUNK_INDEX] = chunk_index
        if isinstance(blocks, list):
            attrs[ATTR_ENTITY_BLOCKS] = blocks_to_json(_entity_blocks(blocks))
        self._record_immediate_span("entity.chunk", attrs)

    def _record_entity_end(self, event: RuntimeEvent) -> None:
        entity_id = _string_or_none(event.data.get("entity_id"))
        entity = self._entities.pop(entity_id or "", None)
        status = _string_or_none(event.data.get("status")) or "completed"
        if entity is not None:
            entity.end(status)
            return
        attrs = self._entity_attrs(event)
        attrs[ATTR_ENTITY_STATUS] = status
        self._record_immediate_span("entity.end", attrs)

    def _record_immediate_span(self, name: str, attrs: OtelAttributes) -> None:
        tracer = trace.get_tracer(_TRACER_NAME)
        span = tracer.start_span(name)
        try:
            for key, value in attrs.items():
                span.set_attribute(key, value)
        finally:
            span.end()

    def _record_initial_history(self, event: RuntimeEvent) -> None:
        conv = self._conversations.get(event.agent_id)
        if conv is None:
            return
        history = event.data.get("history")
        if not isinstance(history, list):
            return
        for item in history:
            if isinstance(item, yuullm.Message):
                role = item.role
                content = item.content
            elif isinstance(item, Mapping):
                role = item.get("role")
                content = item.get("content")
            else:
                continue
            if role != "system":
                continue
            if isinstance(content, list):
                turn = conv.start_turn("system")
                turn.add(*content)
                turn.end()
            return

    def _conversation_id(self, event: RuntimeEvent) -> UUID | None:
        value = self.context_provider.conversation_id(event)
        if isinstance(value, UUID):
            return value
        if isinstance(value, str) and value:
            return UUID(value)
        return None

    def _event_attributes(self, event: RuntimeEvent) -> OtelAttributes:
        attrs: OtelAttributes = {}
        for key, value in self.context_provider.event_attributes(event).items():
            attrs[key] = value
        return attrs

    def _entity_attrs(self, event: RuntimeEvent) -> OtelAttributes:
        attrs = self._event_attributes(event)
        entity_id = event.data.get("entity_id")
        entity_type = event.data.get("entity_type")
        parent_id = event.data.get("parent_id")
        tool_call_id = event.data.get("tool_call_id")
        if isinstance(entity_id, str):
            attrs[ATTR_ENTITY_ID] = entity_id
        if isinstance(entity_type, str):
            attrs[ATTR_ENTITY_TYPE] = entity_type
        if isinstance(parent_id, str) and parent_id:
            attrs[ATTR_ENTITY_PARENT_ID] = parent_id
        if isinstance(tool_call_id, str) and tool_call_id:
            attrs[ATTR_ENTITY_TOOL_CALL_ID] = tool_call_id
        return attrs

    def _conversation_for_entity_event(
        self,
        event: RuntimeEvent,
    ) -> yuutrace.ConversationContext | None:
        parent_id = _string_or_none(event.data.get("parent_id"))
        entity_id = _string_or_none(event.data.get("entity_id"))
        if parent_id:
            return self._conversations.get(parent_id)
        if entity_id:
            return self._conversations.get(entity_id)
        return self._conversations.get(event.agent_id)


def _string_or_none(value: EventValue | None) -> str | None:
    return value if isinstance(value, str) and value else None


def _attribute_value(value: EventValue) -> AttributeValue | None:
    if isinstance(value, str | int | float | bool):
        return value
    return None


def _entity_blocks(blocks: list[object]) -> list[EntityLogBlock]:
    return [
        block
        for block in blocks
        if isinstance(block, ContentBlock | ProcessBlock | CommandBlock)
    ]
