"""Usage recording wrappers.

Business code should use ``record_llm_usage()`` or ``record_tool_usage()``
to record incremental usage events.  These functions handle OTEL
event naming and attribute serialization internally.
"""

from __future__ import annotations

from typing import overload

from ._typing import SupportsLlmCost, SupportsLlmUsage
from .init import should_trace
from .otel import (
    EVENT_LLM_USAGE,
    EVENT_TOOL_USAGE,
    llm_usage_to_otel,
    tool_usage_to_otel,
)
from .span import add_event
from .types import LlmUsageDelta, ToolUsageDelta


# ---------------------------------------------------------------------------
# LLM usage
# ---------------------------------------------------------------------------


def _to_llm_usage_delta(obj: SupportsLlmUsage) -> LlmUsageDelta:
    """Convert any object with matching attributes to LlmUsageDelta.

    Works with yuullm.Usage or any duck-typed equivalent.
    """
    return LlmUsageDelta(
        provider=obj.provider,  # type: ignore[attr-defined]
        model=obj.model,  # type: ignore[attr-defined]
        request_id=getattr(obj, "request_id", None),
        input_tokens=getattr(obj, "input_tokens", 0),
        output_tokens=getattr(obj, "output_tokens", 0),
        cache_read_tokens=getattr(obj, "cache_read_tokens", 0),
        cache_write_tokens=getattr(obj, "cache_write_tokens", 0),
        total_tokens=getattr(obj, "total_tokens", None),
    )


@overload
def record_llm_usage(
    usage: LlmUsageDelta, *, cost: SupportsLlmCost | None = None
) -> None: ...


@overload
def record_llm_usage(
    usage: SupportsLlmUsage, *, cost: SupportsLlmCost | None = None
) -> None: ...


@overload
def record_llm_usage(
    *,
    provider: str,
    model: str,
    request_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    total_tokens: int | None = None,
    cost: SupportsLlmCost | None = None,
) -> None: ...


def record_llm_usage(
    usage: LlmUsageDelta | SupportsLlmUsage | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    request_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    total_tokens: int | None = None,
    cost: SupportsLlmCost | None = None,
) -> None:
    """Record request-level LLM usage, and optional cost, on the current span.

    Accepts a ``LlmUsageDelta``, any duck-typed object with ``provider``
    and ``model`` attributes (e.g. ``yuullm.Usage``), or keyword arguments.
    Pass ``cost=yuullm.Cost`` to record the matching LLM cost event without
    constructing a trace-specific ``CostDelta``.

    If tracing is unconfigured or explicitly disabled, this is a no-op.
    Once tracing is configured, ``NoActiveSpanError`` is raised if there is
    no active recording span.

    Raises ``TypeError`` if neither a struct instance nor the required keyword
    arguments (``provider``, ``model``) are supplied.
    """
    if not should_trace():
        return

    if usage is not None:
        if not isinstance(usage, LlmUsageDelta):
            usage = _to_llm_usage_delta(usage)
    elif provider is not None and model is not None:
        usage = LlmUsageDelta(
            provider=provider,
            model=model,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            total_tokens=total_tokens,
        )
    else:
        raise TypeError(
            "record_llm_usage() requires either a LlmUsageDelta instance, "
            "a duck-typed usage object, or 'provider' and 'model' keyword arguments."
        )
    add_event(EVENT_LLM_USAGE, llm_usage_to_otel(usage))
    if cost is not None:
        from .cost import llm_cost_to_delta, record_cost_delta

        record_cost_delta(llm_cost_to_delta(usage, cost))


# ---------------------------------------------------------------------------
# Tool usage
# ---------------------------------------------------------------------------


def record_tool_usage(usage: ToolUsageDelta) -> None:
    """Record an incremental tool usage event on the current span.

    Only record when the tool has a meaningful, well-defined usage metric
    (e.g. bytes transferred, seconds elapsed, API request count).

    Parameters
    ----------
    usage:
        A fully constructed ``ToolUsageDelta`` instance.

    If tracing is unconfigured or explicitly disabled, this is a no-op.
    Once tracing is configured, ``NoActiveSpanError`` is raised if there is
    no active recording span.
    """
    if not should_trace():
        return
    add_event(EVENT_TOOL_USAGE, tool_usage_to_otel(usage))
