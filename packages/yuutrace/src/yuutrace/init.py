from __future__ import annotations

import atexit
from collections.abc import Sequence
import warnings
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult

if TYPE_CHECKING:
    from .memory import MemoryTraceStore


DEFAULT_ENDPOINT = "http://localhost:4318/v1/traces"
_explicitly_disabled = False
_warned_implicit_noop = False


class _QuietExporter(SpanExporter):
    """Wrapper that suppresses export errors instead of crashing the process.

    Network failures during span export should not propagate to business code.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self._inner.export(spans)
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:
            return


class TracingNotInitializedError(RuntimeError):
    """Compatibility error for callers that still fail-fast explicitly.

    yuutrace now treats unconfigured tracing as an implicit no-op. This class is
    retained for backward compatibility with code that imports it.
    """


def init(
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    service_name: str = "yuutrace",
    service_version: str | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    """Initialize the OTLP trace exporter.

    Sets up a ``TracerProvider`` with a ``BatchSpanProcessor`` that exports
    finished spans to the given OTLP/HTTP endpoint.  If OpenTelemetry is
    already configured (i.e. a non-proxy ``TracerProvider`` exists), this is a
    no-op so yuutrace can coexist with existing instrumentation.

    Parameters
    ----------
    endpoint:
        OTLP/HTTP endpoint URL (default ``http://localhost:4318/v1/traces``).
    service_name:
        ``service.name`` resource attribute (default ``"yuutrace"``).
    service_version:
        Optional ``service.version`` resource attribute.
    timeout_seconds:
        HTTP export timeout in seconds (default ``10.0``).
    """
    global _explicitly_disabled, _warned_implicit_noop
    _explicitly_disabled = False
    _warned_implicit_noop = False

    provider = trace.get_tracer_provider()
    if not _is_proxy_tracer_provider(provider):
        return

    resource_attrs: dict[str, str] = {"service.name": service_name}
    if service_version is not None:
        resource_attrs["service.version"] = service_version
    resource = Resource.create(resource_attrs)

    tracer_provider = TracerProvider(resource=resource)
    import requests

    session = requests.Session()
    session.trust_env = False
    exporter = _QuietExporter(
        OTLPSpanExporter(endpoint=endpoint, timeout=timeout_seconds, session=session)
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(tracer_provider)
    atexit.register(tracer_provider.shutdown)


def disable() -> None:
    """Disable yuutrace instrumentation without touching the OTEL provider.

    Use this when tracing is intentionally off. yuutrace operations become
    no-ops and no "not initialized" warning is emitted.
    """
    global _explicitly_disabled, _warned_implicit_noop
    _explicitly_disabled = True
    _warned_implicit_noop = False


def is_initialized() -> bool:
    return not _is_proxy_tracer_provider(trace.get_tracer_provider())


def is_disabled() -> bool:
    """Return whether yuutrace was explicitly disabled via ``disable()``."""
    return _explicitly_disabled


def is_enabled() -> bool:
    """Return whether yuutrace operations will record spans/events."""
    return not _explicitly_disabled and is_initialized()


def should_trace() -> bool:
    """Return whether a yuutrace operation should record.

    If tracing is neither configured nor explicitly disabled, warn once and
    treat the operation as a no-op.
    """
    if _explicitly_disabled:
        return False
    if is_initialized():
        return True
    _warn_implicit_noop_once()
    return False


def require_initialized() -> None:
    if should_trace():
        return
    raise TracingNotInitializedError(
        "Tracing is not initialized. "
        "Call yuutrace.init(...) at process startup, "
        "or configure OpenTelemetry by setting a TracerProvider "
        "(trace.set_tracer_provider(...)) before using ytrace.conversation()."
    )


def _warn_implicit_noop_once() -> None:
    global _warned_implicit_noop
    if _warned_implicit_noop:
        return
    _warned_implicit_noop = True
    warnings.warn(
        "yuutrace is not initialized; tracing operations are no-ops. "
        "Call yuutrace.init(...) to enable tracing or yuutrace.disable() "
        "to silence this warning.",
        RuntimeWarning,
        stacklevel=3,
    )


def _is_proxy_tracer_provider(provider: trace.TracerProvider) -> bool:
    return provider.__class__.__name__ == "ProxyTracerProvider"


def init_memory() -> MemoryTraceStore:
    """Initialize tracing with an in-memory SQLite backend.

    Returns a MemoryTraceStore that can be queried for spans/conversations.
    Useful for testing: assert on recorded traces without external collector.

    This forcibly replaces any existing TracerProvider.
    """
    global _explicitly_disabled, _warned_implicit_noop
    _explicitly_disabled = False
    _warned_implicit_noop = False

    import sqlite3

    from .cli.db import _SCHEMA
    from .memory import MemoryTraceStore, _MemoryExporter

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("PRAGMA foreign_keys=ON")

    # Shutdown existing provider if any
    existing = trace.get_tracer_provider()
    if hasattr(existing, "shutdown"):
        try:
            existing.shutdown()
        except Exception:
            pass

    provider = TracerProvider(resource=Resource.create({"service.name": "yuutrace-test"}))
    exporter = _MemoryExporter(conn)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Force-set even if already configured
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)

    return MemoryTraceStore(conn)
