"""HTTP routes for the Echo integration."""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from yuubot.core.integrations.core import IntegrationCore
from yuubot.core.integrations.impls.echo import EchoIngressPayload, EchoIntegration
from yuubot.runtime.http_utils import error_response


def _with_echo_error_handling(
    func: Callable[..., Awaitable[JSONResponse]],
) -> Callable[..., Awaitable[JSONResponse]]:
    """Decorate echo handlers with consistent exception→HTTP status mapping.

    LookupError  → 404
    ValueError   → 400
    Exception    → 500
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> JSONResponse:
        try:
            return await func(*args, **kwargs)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:
            return error_response(str(exc), status_code=500)

    return wrapper


def echo_routes(integrations: IntegrationCore) -> list[Route]:
    """Build Starlette routes for the echo integration.

    Mounted by the daemon under ``/integration/echo``.
    """

    async def handle_ingress(request: Request) -> JSONResponse:
        return await _echo_ingress(request, integrations)

    async def handle_round_trip(request: Request) -> JSONResponse:
        return await _echo_round_trip(request, integrations)

    return [
        Route("/round-trip", handle_round_trip, methods=("POST",)),
        Route("/", handle_ingress, methods=("POST",)),
    ]


# ---------------------------------------------------------------------------
# Module-level handlers
# ---------------------------------------------------------------------------


@_with_echo_error_handling
async def _echo_ingress(
    request: Request,
    integrations: IntegrationCore,
) -> JSONResponse:
    payload_or_response = await _payload_from_request(request)
    if isinstance(payload_or_response, JSONResponse):
        return payload_or_response

    payload = payload_or_response
    instance = _resolve_instance(integrations, payload.integration_id)
    message = await instance.emit_payload(payload)

    return JSONResponse(
        {
            "status": "ok",
            "integration_id": instance.ingress.integration_id,
            "message_id": message.message_id,
            "source": msgspec.to_builtins(message.source),
        },
        status_code=202,
    )


@_with_echo_error_handling
async def _echo_round_trip(
    request: Request,
    integrations: IntegrationCore,
) -> JSONResponse:
    round_trip_or_response = await _round_trip_from_request(request)
    if isinstance(round_trip_or_response, JSONResponse):
        return round_trip_or_response

    payload, timeout_s = round_trip_or_response
    instance = _resolve_instance(integrations, payload.integration_id)
    message = await instance.emit_payload(payload)

    try:
        reply = await instance.wait_for_reply(timeout_s)
    except TimeoutError:
        return error_response("echo round-trip timed out", status_code=504)

    return JSONResponse(
        {
            "status": "ok",
            "integration_id": instance.ingress.integration_id,
            "message_id": message.message_id,
            "source": msgspec.to_builtins(message.source),
            "reply": msgspec.to_builtins(reply),
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# Request parsing helpers
# ---------------------------------------------------------------------------


async def _payload_from_request(
    request: Request,
) -> EchoIngressPayload | JSONResponse:
    body_or_response = await _request_body(request)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    return _payload_from_body(body_or_response)


async def _round_trip_from_request(
    request: Request,
) -> tuple[EchoIngressPayload, float] | JSONResponse:
    body_or_response = await _request_body(request)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    payload_or_response = _payload_from_body(body_or_response)
    if isinstance(payload_or_response, JSONResponse):
        return payload_or_response
    try:
        timeout_s = _round_trip_timeout_s(body_or_response)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    return payload_or_response, timeout_s


async def _request_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error_response("request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return error_response("request body must be a JSON object", status_code=400)
    return cast(dict[str, Any], payload)


def _payload_from_body(
    payload: dict[str, Any],
) -> EchoIngressPayload | JSONResponse:
    try:
        return msgspec.convert(
            payload,
            type=EchoIngressPayload,
            strict=False,
        )
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        return error_response(str(exc), status_code=400)


def _round_trip_timeout_s(payload: dict[str, Any]) -> float:
    raw_timeout = payload.get("timeout_s", 10.0)
    if not isinstance(raw_timeout, int | float) or isinstance(raw_timeout, bool):
        raise ValueError("timeout_s must be a number")
    timeout_s = float(raw_timeout)
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    return min(timeout_s, 60.0)


def _resolve_instance(
    integrations: IntegrationCore,
    integration_id: str,
) -> EchoIntegration:
    if integration_id:
        instance = integrations.running_instance(integration_id)
        if not isinstance(instance, EchoIntegration):
            raise LookupError(f"integration {integration_id!r} is not an echo integration")
        return instance

    matches: list[EchoIntegration] = []
    for running_id in integrations.running_integration_ids():
        instance = integrations.running_instance(running_id)
        if isinstance(instance, EchoIntegration):
            matches.append(instance)
    if not matches:
        raise LookupError("no running echo integration")
    if len(matches) > 1:
        raise ValueError("integration_id is required when multiple echo integrations run")
    return matches[0]
