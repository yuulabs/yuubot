"""Low-level span access and event recording.

Provides ``current_span()`` and ``add_event()`` -- the building blocks
on which the higher-level wrappers in ``cost.py`` and ``usage.py`` are built.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from .init import should_trace
from .otel import OtelAttributes

_TRACER_NAME = "yuutrace"


class NoActiveSpanError(RuntimeError):
    """Raised when ``current_span()`` finds no active span.

    Once tracing is configured, callers must ensure a span is active before
    writing observability data. If yuutrace is unconfigured or explicitly
    disabled, operations are no-ops instead.
    """


class TraceSpan:
    """Small writer yielded by ``trace_span`` for adding span attributes."""

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def attrs(self, **attributes: object) -> None:
        """Set OTEL-compatible attributes, ignoring unsupported values."""
        if not self._span.is_recording():
            return
        for key, value in attributes.items():
            if isinstance(value, str | int | float | bool):
                self._span.set_attribute(key, value)


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


@contextmanager
def trace_span(
    name: str,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[TraceSpan]:
    """Record a short-lived yuutrace span.

    This is the low-level primitive for instrumenting non-conversation work
    that still belongs in the yuutrace OTEL stream. When tracing is disabled
    or unconfigured, it yields ``trace.INVALID_SPAN`` and records nothing.
    """
    if not should_trace():
        yield TraceSpan(trace.INVALID_SPAN)
        return

    tracer = trace.get_tracer(_TRACER_NAME)
    span = tracer.start_span(name)
    trace_span = TraceSpan(span)
    activation = trace.use_span(
        span,
        end_on_exit=False,
        record_exception=False,
        set_status_on_exception=False,
    )
    activation.__enter__()
    try:
        if attributes is not None:
            trace_span.attrs(**dict(attributes))
        yield trace_span
    except BaseException as error:
        set_span_error(span, error)
        raise
    finally:
        activation.__exit__(None, None, None)
        span.end()


def set_span_error(span: Span, error: BaseException) -> None:
    """Mark the given span as errored with the given exception."""
    if not span.is_recording():
        return
    span.set_status(StatusCode.ERROR, str(error))
    span.record_exception(error)
