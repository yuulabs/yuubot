"""High-level conversation tracing context managers.

Provides the Turn-based API for recording conversation messages:

- ``TurnContext`` — a single conversational turn (user or assistant)
- ``ConversationContext`` — wraps a conversation span
- ``ToolsContext`` / ``ToolSpan`` — tool batch execution spans
- ``conversation()`` / ``start_conversation()`` — entry points
"""

from __future__ import annotations

from contextlib import AbstractContextManager
import json
import time
from types import TracebackType
from typing import Any, cast
from uuid import UUID

import msgspec
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, Span, StatusCode
from opentelemetry.util.types import AttributeValue
import yuullm

from .init import should_trace
from .otel import (
    ATTR_AGENT,
    ATTR_CONTEXT_SYSTEM_TOOLS,
    ATTR_CONVERSATION_ID,
    ATTR_CONVERSATION_MODEL,
    ATTR_CONVERSATION_TAGS,
    ATTR_ENTITY_BLOCKS,
    ATTR_ENTITY_CHUNK_INDEX,
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PARENT_ID,
    ATTR_ENTITY_STATUS,
    ATTR_ENTITY_TOOL_CALL_ID,
    ATTR_ENTITY_TYPE,
    ATTR_TOOL_CALL_ID,
    ATTR_TOOL_ERROR,
    ATTR_TOOL_INPUT,
    ATTR_TOOL_NAME,
    ATTR_TOOL_OUTPUT,
    ATTR_TURN_ITEMS,
    ATTR_TURN_ROLE,
    ATTR_TURN_START_TIME,
    EVENT_COST,
    EVENT_LLM_USAGE,
    cost_delta_to_otel,
    llm_usage_to_otel,
)
from .span import set_span_error
from .types import LlmCost, LlmUsage, LlmUsageDelta

_TRACER_NAME = "yuutrace"
type SpanActivation = AbstractContextManager[Span]


# ---------------------------------------------------------------------------
# Span activation helpers
# ---------------------------------------------------------------------------


def _activate_span(span: Span) -> SpanActivation | None:
    if not span.is_recording():
        return None
    activation = cast(
        SpanActivation,
        trace.use_span(
            span,
            end_on_exit=False,
            record_exception=False,
            set_status_on_exception=False,
        ),
    )
    activation.__enter__()
    return activation


def _deactivate_span(
    activation: SpanActivation | None,
    exc_type: type[BaseException] | None,
    exc_val: BaseException | None,
    exc_tb: TracebackType | None,
) -> None:
    if activation is not None:
        activation.__exit__(exc_type, exc_val, exc_tb)


# ---------------------------------------------------------------------------
# JSON serialization helpers
#
# NOTE: The types in this section (Any, object) are intentionally broad.
# The SDK public API accepts arbitrary user-provided objects that get
# JSON-serialized at runtime (msgspec Structs, yuullm items, str, dict,
# etc.).  Using a fake "Json*" type system (e.g. JsonSerializable = Any)
# adds indirection without adding safety — the data is genuinely untyped
# at this boundary.  DO NOT re-introduce Json* type aliases here.
# ---------------------------------------------------------------------------


def _struct_to_json(value: msgspec.Struct) -> object:
    if isinstance(value, yuullm.Message):
        return [_serialize_item(item) for item in value.content]
    return cast(object, msgspec.to_builtins(value))


def _json_default(obj: object) -> object:
    """Fallback for ``json.dumps`` that handles msgspec Structs."""
    if isinstance(obj, msgspec.Struct):
        return _struct_to_json(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _serialize_item(item: Any) -> object:
    """Normalize a yuullm message or content item to JSON-friendly data."""
    if isinstance(item, msgspec.Struct):
        return _struct_to_json(item)
    return item


def _items_to_json(items: list[Any]) -> str:
    """Serialize a list of items to a JSON string."""
    serialized: list[object] = []
    for item in items:
        value = _serialize_item(item)
        if isinstance(value, list):
            serialized.extend(value)
        else:
            serialized.append(value)
    return json.dumps(
        serialized,
        ensure_ascii=False,
        default=_json_default,
    )


# ---------------------------------------------------------------------------
# EntityContext
# ---------------------------------------------------------------------------


class EntityContext:
    """Append-only entity trace writer.

    Entity metadata, chunks, and end markers are recorded as short-lived spans
    that end when the entity is started, flushed, or ended.  The active OTEL
    span processor controls when those finished spans are exported or persisted.
    """

    __slots__ = (
        "_trace_ctx",
        "_entity_id",
        "_entity_type",
        "_parent_id",
        "_tool_call_id",
        "_chunk_index",
        "_ended",
    )

    def __init__(
        self,
        trace_ctx: object | None,
        *,
        entity_id: str,
        entity_type: str,
        parent_id: str = "",
        tool_call_id: str | None = None,
    ) -> None:
        self._trace_ctx = trace_ctx
        self._entity_id = entity_id
        self._entity_type = entity_type
        self._parent_id = parent_id
        self._tool_call_id = tool_call_id
        self._chunk_index = 0
        self._ended = False
        self._record("entity", self._base_attrs())

    def flush(self, blocks: list[Any]) -> None:
        """Persist one chunk of already-coalesced entity output blocks."""
        if not blocks:
            return
        attrs = self._base_attrs()
        attrs[ATTR_ENTITY_CHUNK_INDEX] = self._chunk_index
        attrs[ATTR_ENTITY_BLOCKS] = _items_to_json(blocks)
        self._chunk_index += 1
        self._record("entity.chunk", attrs)

    def end(self, status: str = "completed") -> None:
        """Persist the entity end marker once."""
        if self._ended:
            return
        self._ended = True
        attrs = self._base_attrs()
        attrs[ATTR_ENTITY_STATUS] = status
        self._record("entity.end", attrs)

    def _base_attrs(self) -> dict[str, AttributeValue]:
        attrs: dict[str, AttributeValue] = {
            ATTR_ENTITY_ID: self._entity_id,
            ATTR_ENTITY_TYPE: self._entity_type,
        }
        if self._parent_id:
            attrs[ATTR_ENTITY_PARENT_ID] = self._parent_id
        if self._tool_call_id:
            attrs[ATTR_ENTITY_TOOL_CALL_ID] = self._tool_call_id
        return attrs

    def _record(self, name: str, attrs: dict[str, AttributeValue]) -> None:
        if self._trace_ctx is None:
            return
        tracer = trace.get_tracer(_TRACER_NAME)
        span = tracer.start_span(name, context=cast(Any, self._trace_ctx))
        try:
            for key, value in attrs.items():
                span.set_attribute(key, value)
        finally:
            span.end()


# ---------------------------------------------------------------------------
# TurnContext
# ---------------------------------------------------------------------------


class TurnContext:
    """A single conversational turn (user or assistant).

    Each turn is recorded as an independent child span (named ``"turn"``).
    Calling ``end()`` finishes the span; the active OTEL span processor controls
    when the finished span is exported or persisted.

    Usage::

        # Manual lifecycle
        turn = chat.start_turn("assistant")
        turn.add(*items)
        turn.usage(usage_obj, cost=cost_obj)
        turn.end()

        # Context manager
        with chat.turn("user") as t:
            t.add(text_item, image_item)
    """

    __slots__ = ("_span", "_role", "_items", "_ended", "_start_ns", "_activation")

    def __init__(self, trace_ctx: object | None, role: str, conversation_id: str) -> None:
        if trace_ctx is not None:
            tracer = trace.get_tracer(_TRACER_NAME)
            self._span = tracer.start_span("turn", context=cast(Any, trace_ctx))
            self._span.set_attribute(ATTR_CONVERSATION_ID, conversation_id)
            self._span.set_attribute(ATTR_TURN_ROLE, role)
        else:
            self._span = trace.INVALID_SPAN
        self._role = role
        self._items: list[Any] = []
        self._ended = False
        self._start_ns = time.time_ns()
        self._activation: SpanActivation | None = None

    def add(self, *items: Any) -> None:
        """Append content items (yuullm.Item dicts or msgspec Structs)."""
        self._items.extend(items)

    def usage(self, u: LlmUsageDelta | LlmUsage, cost: LlmCost | None = None) -> None:
        """Record token usage (and optional cost) for this turn.

        Emits ``yuu.llm.usage`` (and optionally ``yuu.cost``) events on the
        turn's own child span.  Also stores the attributes directly on the span
        so the frontend can render per-turn metrics without parsing events.
        """
        if not self._span.is_recording():
            return

        from .usage import _to_llm_usage_delta

        from .cost import llm_cost_to_delta

        if not isinstance(u, LlmUsageDelta):
            u = _to_llm_usage_delta(u)

        # Emit usage event on the turn span
        usage_otel = llm_usage_to_otel(u)
        self._span.add_event(EVENT_LLM_USAGE, attributes=usage_otel)
        # Also set as span attributes for direct access
        for k, v in usage_otel.items():
            self._span.set_attribute(k, v)

        # Emit cost event if provided
        if cost is not None:
            cost_delta = llm_cost_to_delta(u, cost)
            cost_otel = cost_delta_to_otel(cost_delta)
            self._span.add_event(EVENT_COST, attributes=cost_otel)
            for k, v in cost_otel.items():
                self._span.set_attribute(k, v)

    def end(self, error: Exception | None = None) -> None:
        """End the turn's child span."""
        if self._ended:
            return
        self._ended = True

        self._span.set_attribute(ATTR_TURN_ITEMS, _items_to_json(self._items))
        self._span.set_attribute(ATTR_TURN_START_TIME, self._start_ns)

        if error is not None:
            set_span_error(self._span, error)

        self._span.end()

    def __enter__(self) -> TurnContext:
        self._activation = _activate_span(self._span)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.end(error=exc_val if isinstance(exc_val, Exception) else None)
        _deactivate_span(self._activation, exc_type, exc_val, exc_tb)
        return False


# ---------------------------------------------------------------------------
# ToolSpan
# ---------------------------------------------------------------------------


class ToolSpan:
    """Wraps a child span for a single tool invocation.

    Usage::

        # Context manager
        with tools.tool(name="search", call_id="tc_1", input={"q": "x"}) as ts:
            ts.ok("result")

        # Manual
        ts = tools.start_tool(name="search", call_id="tc_1", input={"q": "x"})
        ts.ok("result")
        ts.end()
    """

    __slots__ = ("_span", "_ended", "_activation")

    def __init__(self, span: Span) -> None:
        self._span = span
        self._ended = False
        self._activation: SpanActivation | None = None

    def ok(self, result: Any) -> None:
        """Record a successful tool result."""
        if not self._span.is_recording():
            return
        output = result if isinstance(result, str) else json.dumps(result, default=_json_default)
        self._span.set_attribute(ATTR_TOOL_OUTPUT, output)

    def fail(self, error: str) -> None:
        """Record a tool error."""
        if not self._span.is_recording():
            return
        self._span.set_attribute(ATTR_TOOL_ERROR, error)
        self._span.set_status(StatusCode.ERROR, error)

    def end(self) -> None:
        """End the tool span."""
        if self._ended:
            return
        self._ended = True
        self._span.end()

    def __enter__(self) -> ToolSpan:
        self._activation = _activate_span(self._span)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_val is not None:
            self.fail(f"{type(exc_val).__name__}: {exc_val}")
        self.end()
        _deactivate_span(self._activation, exc_type, exc_val, exc_tb)
        return False


# ---------------------------------------------------------------------------
# ToolsContext
# ---------------------------------------------------------------------------


class ToolsContext:
    """Wraps a child span for a batch of tool executions.

    Usage::

        # Context manager
        with chat.tool_batch() as tools:
            with tools.tool(name="search", call_id="tc_1", input={}) as ts:
                ts.ok("ok")

        # Manual
        tools = chat.start_tools()
        ts = tools.start_tool(name="search", call_id="tc_1", input={})
        ts.ok("ok")
        ts.end()
        tools.end()
    """

    __slots__ = ("_span", "_conversation_id", "_ended", "_activation")

    def __init__(self, span: Span, conversation_id: str) -> None:
        self._span = span
        self._conversation_id = conversation_id
        self._ended = False
        self._activation: SpanActivation | None = None

    def start_tool(
        self,
        *,
        name: str,
        call_id: str,
        input: Any,  # noqa: A002
    ) -> ToolSpan:
        """Start a child span for a single tool invocation."""
        if not self._span.is_recording():
            return ToolSpan(trace.INVALID_SPAN)
        tracer = trace.get_tracer(_TRACER_NAME)
        tool_span = tracer.start_span(
            f"tool:{name}",
            context=trace.set_span_in_context(self._span),
        )
        tool_span.set_attribute(ATTR_TOOL_NAME, name)
        tool_span.set_attribute(ATTR_TOOL_CALL_ID, call_id)
        tool_span.set_attribute(ATTR_CONVERSATION_ID, self._conversation_id)
        input_str = input if isinstance(input, str) else json.dumps(input, default=_json_default)
        tool_span.set_attribute(ATTR_TOOL_INPUT, input_str)
        return ToolSpan(tool_span)

    def tool(
        self,
        *,
        name: str,
        call_id: str,
        input: Any,  # noqa: A002
    ) -> ToolSpan:
        """Context manager shortcut for ``start_tool``."""
        return self.start_tool(name=name, call_id=call_id, input=input)

    def end(self) -> None:
        """End the tools batch span."""
        if self._ended:
            return
        self._ended = True
        self._span.end()

    def __enter__(self) -> ToolsContext:
        self._activation = _activate_span(self._span)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.end()
        _deactivate_span(self._activation, exc_type, exc_val, exc_tb)
        return False


# ---------------------------------------------------------------------------
# ConversationContext
# ---------------------------------------------------------------------------


class ConversationContext:
    """Tracks a conversation via a shared trace context.

    The "conversation" span is ended during construction after recording agent
    name, model, and conversation ID.  All subsequent turn and tool spans are
    created as children of that span so they share the same trace_id.  Export
    timing is determined by the active OTEL span processor.

    Usage::

        with ytrace.conversation(id=uuid, agent="a", model="m") as chat:
            chat.system("You are helpful.", tools=[...])
            chat.user(text_item, image_item)

            with chat.turn("assistant") as t:
                t.add(*response_items)
                t.usage(usage)

            with chat.tool_batch() as batch:
                with batch.tool(name="search", call_id="tc_1", input={}) as ts:
                    ts.ok("result")
    """

    __slots__ = ("_trace_ctx", "_conversation_id", "_ended")

    def __init__(self, trace_ctx: object | None, conversation_id: str) -> None:
        self._trace_ctx = trace_ctx  # None means tracing disabled
        self._conversation_id = conversation_id
        self._ended = False

    # -- Turn API -----------------------------------------------------------

    def start_turn(self, role: str) -> TurnContext:
        """Start a turn manually. Caller must call ``turn.end()``."""
        return TurnContext(self._trace_ctx, role, self._conversation_id)

    def turn(self, role: str) -> TurnContext:
        """Context manager for a turn."""
        return TurnContext(self._trace_ctx, role, self._conversation_id)

    def start_entity(
        self,
        *,
        entity_id: str,
        entity_type: str,
        parent_id: str = "",
        tool_call_id: str | None = None,
    ) -> EntityContext:
        """Start an entity writer. Caller should call ``entity.end()``."""
        return EntityContext(
            self._trace_ctx,
            entity_id=entity_id,
            entity_type=entity_type,
            parent_id=parent_id,
            tool_call_id=tool_call_id,
        )

    def user(self, *items: Any) -> None:
        """Sugar: record an instant user turn.

        Accepts ``yuullm.Item`` dicts, ``msgspec.Struct`` instances, or plain
        ``str`` (auto-wrapped to ``TextItem``).
        """
        if self._trace_ctx is None:
            return
        normalized = [
            {"type": "text", "text": it} if isinstance(it, str) else it
            for it in items
        ]
        t = self.start_turn("user")
        t.add(*normalized)
        t.end()

    # -- System prompt ------------------------------------------------------

    def system(self, persona: str, tools: Any | None = None) -> None:
        """Emit a system turn span."""
        if self._trace_ctx is None:
            return
        t = self.start_turn("system")
        if persona:
            t.add({"type": "text", "text": persona})
        if tools is not None:
            tools_str = tools if isinstance(tools, str) else json.dumps(tools, default=_json_default)
            t._span.set_attribute(ATTR_CONTEXT_SYSTEM_TOOLS, tools_str)
        t.end()

    # -- Tool batch ---------------------------------------------------------

    def start_tool_batch(self) -> ToolsContext:
        """Start a tool execution batch span."""
        if self._trace_ctx is None:
            return ToolsContext(trace.INVALID_SPAN, self._conversation_id)
        tracer = trace.get_tracer(_TRACER_NAME)
        tools_span = tracer.start_span("tools", context=cast(Any, self._trace_ctx))
        tools_span.set_attribute(ATTR_CONVERSATION_ID, self._conversation_id)
        return ToolsContext(tools_span, self._conversation_id)

    def tool_batch(self) -> ToolsContext:
        """Context manager version of ``start_tool_batch()``."""
        return self.start_tool_batch()

    def start_tools(self) -> ToolsContext:
        """Compatibility alias for ``start_tool_batch()``."""
        return self.start_tool_batch()

    def tools(self) -> ToolsContext:
        """Compatibility alias for ``tool_batch()``."""
        return self.start_tool_batch()

    # -- Lifecycle ----------------------------------------------------------

    def end(self, error: Exception | None = None) -> None:
        """No-op: the conversation span was already ended during construction."""
        self._ended = True

    def __enter__(self) -> ConversationContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.end(error=exc_val if isinstance(exc_val, Exception) else None)
        return False


# ---------------------------------------------------------------------------
# Module-level entry points
# ---------------------------------------------------------------------------


def start_conversation(
    *,
    id: UUID,  # noqa: A002
    agent: str,
    model: str,
    tags: list[str] | None = None,
) -> ConversationContext:
    """Start a conversation (manual lifecycle).

    Emits a short-lived "conversation" span carrying agent, model, and
    conversation ID, then ends it during construction.  All subsequent turn and
    tool spans become children of that span via a shared trace context.  Export
    timing is determined by the active OTEL span processor.

    Caller may call ``.end()`` when done, but it is a no-op.
    """
    conversation_id = str(id)
    if not should_trace():
        return ConversationContext(None, conversation_id)

    tracer = trace.get_tracer(_TRACER_NAME)
    span = tracer.start_span("conversation")
    span.set_attribute(ATTR_CONVERSATION_ID, conversation_id)
    span.set_attribute(ATTR_AGENT, agent)
    span.set_attribute(ATTR_CONVERSATION_MODEL, model)
    if tags:
        span.set_attribute(ATTR_CONVERSATION_TAGS, tags)

    # Build a non-recording carrier that propagates this span's trace context
    # to all child spans (same trace_id, parent_span_id = this span's ID).
    # The carrier is non-recording so it does not itself appear in the DB.
    span_ctx = span.get_span_context()
    carrier = NonRecordingSpan(span_ctx)
    trace_ctx = trace.set_span_in_context(carrier)

    # End this metadata span now; the configured span processor decides when to export it.
    span.end()

    return ConversationContext(trace_ctx, conversation_id)


def conversation(
    *,
    id: UUID,  # noqa: A002
    agent: str,
    model: str,
    tags: list[str] | None = None,
) -> ConversationContext:
    """Context manager that starts a conversation.

    Usage::

        with ytrace.conversation(id=uuid, agent="a", model="m") as chat:
            chat.user(text_item)
            ...
    """
    return start_conversation(id=id, agent=agent, model=model, tags=tags)
