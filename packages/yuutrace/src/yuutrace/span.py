"""Low-level span access and event recording.

Provides ``current_span()`` and ``add_event()`` -- the building blocks
on which the higher-level wrappers in ``cost.py`` and ``usage.py`` are built.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from .init import should_trace
from .otel import OtelAttributes


class NoActiveSpanError(RuntimeError):
    """Raised when ``current_span()`` finds no active span.

    Once tracing is configured, callers must ensure a span is active before
    writing observability data. If yuutrace is unconfigured or explicitly
    disabled, operations are no-ops instead.
    """


def current_span() -> Span:
    """Return the currently active OTEL span.

    Raises
    ------
    NoActiveSpanError
        If there is no active span (i.e. the returned span is a
        ``NonRecordingSpan`` / ``INVALID_SPAN``).
    """
    if not should_trace():
        return trace.INVALID_SPAN

    span = trace.get_current_span()
    if not span.is_recording():
        raise NoActiveSpanError(
            "No active recording span. "
            "Wrap your code in a yuutrace context manager "
            "(e.g. ytrace.conversation()) before recording events."
        )
    return span


def add_event(name: str, attributes: OtelAttributes) -> None:
    """Add an event to the current span.

    This is the **internal** primitive used by ``record_cost_delta``,
    ``record_llm_usage``, etc.  Business code should use the typed
    wrapper functions instead of calling this directly.

    Raises
    ------
    NoActiveSpanError
        Propagated from ``current_span()`` if no span is active.
    """
    span = current_span()
    if span.is_recording():
        span.add_event(name, attributes=attributes)  # type: ignore[arg-type]


def set_span_error(span: Span, error: BaseException) -> None:
    """Mark the given span as errored with the given exception."""
    if not span.is_recording():
        return
    span.set_status(StatusCode.ERROR, str(error))
    span.record_exception(error)
