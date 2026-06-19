"""Core data types for yuutrace.

Defines the structured types for cost and usage deltas,
aligned with the ytrace_spec.md OTEL data shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

import msgspec


class CostCategory(str, Enum):
    """Category of a cost event."""

    llm = "llm"
    tool = "tool"


class Currency(str, Enum):
    """Supported currencies."""

    USD = "USD"


class LlmUsage(Protocol):
    """Structural request-level LLM usage.

    ``yuullm.Usage`` satisfies this protocol.  yuutrace keeps this protocol
    separate from ``LlmUsageDelta`` so callers can pass the usage object
    produced by their LLM client without constructing trace-specific structs.
    """

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def request_id(self) -> str | None: ...

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def cache_read_tokens(self) -> int: ...

    @property
    def cache_write_tokens(self) -> int: ...

    @property
    def total_tokens(self) -> int | None: ...


class LlmCost(Protocol):
    """Structural request-level LLM cost.

    ``yuullm.Cost`` satisfies this protocol.  Trace events store the total as
    a ``CostDelta`` amount while preserving the pricing source when available.
    """

    @property
    def total_cost(self) -> float: ...


class CostDelta(msgspec.Struct, frozen=True):
    """An incremental cost event.

    All amounts are deltas -- the same span may carry multiple CostDelta
    events.  Aggregation happens at the query layer.
    """

    category: CostCategory
    currency: Currency
    amount: float
    source: str | None = None
    pricing_id: str | None = None
    # LLM-specific (when category == llm)
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_request_id: str | None = None
    # Tool-specific (when category == tool)
    tool_name: str | None = None
    tool_call_id: str | None = None


class LlmUsageDelta(msgspec.Struct, frozen=True):
    """An incremental LLM token usage event.

    Token counts are per-request deltas, never cross-request accumulations.
    """

    provider: str
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int | None = None


class ToolUsageDelta(msgspec.Struct, frozen=True, kw_only=True):
    """An incremental tool usage event.

    Only recorded when a tool has a meaningful usage metric
    (e.g. bytes, seconds, request count).
    """

    name: str
    unit: str
    quantity: float
    call_id: str | None = None
