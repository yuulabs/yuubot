"""yuutrace -- LLM-oriented observability SDK built on OpenTelemetry.

Public API
----------

Types::

    CostCategory, Currency
    CostDelta, LlmCost, LlmUsage, LlmUsageDelta, ToolUsageDelta

Context managers::

    conversation(*, id, agent, model, tags=None) -> ConversationContext
    ConversationContext.turn(role) -> TurnContext
    ConversationContext.start_entity(...) -> EntityContext
    ConversationContext.tool_batch() -> ToolsContext

Recording (recommended wrappers)::

    record_cost(*, category, currency, amount, ...)
    record_cost_delta(cost: CostDelta)
    record_llm_usage(usage_or_kwargs)
    record_tool_usage(usage: ToolUsageDelta)

Initialization::

    init(*, endpoint="http://localhost:4318/v1/traces", service_name="yuutrace", ...)
    disable()
    init_memory() -> MemoryTraceStore

Low-level::

    current_span() -> Span
    trace_span(name, attributes=None)
    add_event(name, attributes)
"""

from __future__ import annotations

# -- Types -----------------------------------------------------------------
from .types import (
    CostCategory,
    CostDelta,
    Currency,
    LlmCost,
    LlmUsage,
    LlmUsageDelta,
    ToolUsageDelta,
)

# -- Context managers ------------------------------------------------------
from .context import (
    ConversationContext,
    EntityContext,
    ToolSpan,
    ToolsContext,
    TurnContext,
    conversation,
    start_conversation,
)

# -- Recording wrappers ----------------------------------------------------
from .cost import record_cost, record_cost_delta, record_llm_cost
from .usage import record_llm_usage, record_tool_usage

# -- Initialization --------------------------------------------------------
from .init import (
    TracingNotInitializedError,
    disable,
    init,
    init_memory,
    is_disabled,
    is_enabled,
    is_initialized,
)
from .memory import MemoryTraceStore

# -- Low-level -------------------------------------------------------------
from .span import NoActiveSpanError, TraceSpan, add_event, current_span, trace_span

__all__ = [
    # Types
    "CostCategory",
    "Currency",
    "CostDelta",
    "LlmCost",
    "LlmUsage",
    "LlmUsageDelta",
    "ToolUsageDelta",
    # Context managers
    "conversation",
    "start_conversation",
    "ConversationContext",
    "TurnContext",
    "EntityContext",
    "ToolsContext",
    "ToolSpan",
    # Recording
    "record_cost",
    "record_cost_delta",
    "record_llm_cost",
    "record_llm_usage",
    "record_tool_usage",
    # Initialization
    "init",
    "disable",
    "init_memory",
    "is_initialized",
    "is_enabled",
    "is_disabled",
    "TracingNotInitializedError",
    "MemoryTraceStore",
    # Low-level
    "current_span",
    "trace_span",
    "add_event",
    "NoActiveSpanError",
    "TraceSpan",
]
