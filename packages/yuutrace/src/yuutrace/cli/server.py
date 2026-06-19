"""OTEL Collector + SQLite storage backend.

Implements ``ytrace server`` -- receives OTLP/HTTP (JSON or Protobuf) trace data
and persists it to a SQLite database via :mod:`yuutrace.cli.db`.

Usage::

    ytrace server --db ./traces.db --port 4318

SDK-side configuration::

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .db import init_db, insert_resource_spans

if TYPE_CHECKING:
    import uvicorn

logger = logging.getLogger("yuutrace.server")


async def _receive_traces(request: Request) -> Response:
    """POST /v1/traces — receive OTLP/HTTP (JSON or Protobuf) and persist to SQLite."""
    content_type = request.headers.get("content-type", "")

    # Determine if this is JSON or Protobuf
    is_protobuf = "application/x-protobuf" in content_type
    is_json = "application/json" in content_type

    # Be lenient: if no content-type, try to detect from content
    if not is_protobuf and not is_json and content_type:
        return JSONResponse(
            {
                "error": "Unsupported content type. Use application/json or application/x-protobuf."
            },
            status_code=415,
        )

    try:
        if is_protobuf or (not is_json and not content_type):
            # Parse Protobuf
            body_bytes = await request.body()
            proto_request = ExportTraceServiceRequest()
            proto_request.ParseFromString(body_bytes)

            # Convert to dict using camelCase to match OTLP JSON format
            # preserving_proto_field_name=False converts to camelCase
            body = MessageToDict(
                proto_request,
                preserving_proto_field_name=False,
                use_integers_for_enums=False,
            )
        else:
            # Parse JSON
            body = await request.json()
    except Exception as exc:
        logger.exception("Failed to parse request body")
        return JSONResponse({"error": f"Invalid request body: {exc}"}, status_code=400)

    resource_spans = body.get("resourceSpans", [])
    if not resource_spans:
        return JSONResponse({"partialSuccess": {}})

    try:
        count = insert_resource_spans(request.app.state.db, resource_spans)
        logger.info("Inserted %d spans", count)
    except Exception:
        logger.exception("Failed to insert spans")
        return JSONResponse({"error": "Internal storage error"}, status_code=500)

    return JSONResponse({"partialSuccess": {}})


def _build_app(db_path: str) -> Starlette:
    """Create the Starlette ASGI application."""
    routes = [
        Route("/v1/traces", _receive_traces, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.db = init_db(db_path)
    return app


_server: "uvicorn.Server | None" = None


def run_server(*, db_path: str, host: str, port: int) -> None:
    """Start the OTEL collector + SQLite storage server.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    host:
        Bind host for the OTEL HTTP receiver.
    port:
        Port for the OTEL HTTP receiver.
    """
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger.info("Starting yuutrace collector on port %d (db: %s)", port, db_path)

    app = _build_app(db_path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    global _server
    _server = uvicorn.Server(config)
    _server.run()


def shutdown_server() -> None:
    """Signal the collector server to shut down gracefully."""
    if _server is not None:
        _server.should_exit = True
