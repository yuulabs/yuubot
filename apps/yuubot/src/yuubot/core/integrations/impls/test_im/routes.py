"""HTTP routes for the Test IM integration.

Mounted by the daemon under ``/integration/test_im``.

Endpoints
---------
- ``POST /`` — Ingress an inbound message on a channel
- ``POST /send`` — Trigger an outbound response (for testing)
"""

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
from yuubot.core.integrations.impls.test_im.integration import (
    TestImIntegration,
)
from yuubot.runtime.http_utils import error_response


def _with_test_im_error_handling(
    func: Callable[..., Awaitable[JSONResponse]],
) -> Callable[..., Awaitable[JSONResponse]]:
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


def test_im_routes(integrations: IntegrationCore) -> list[Route]:
    async def handle_ingress(request: Request) -> JSONResponse:
        return await _test_im_ingress(request, integrations)

    return [
        Route("/", handle_ingress, methods=("POST",)),
    ]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@_with_test_im_error_handling
async def _test_im_ingress(
    request: Request,
    integrations: IntegrationCore,
) -> JSONResponse:
    body = await _request_body(request)
    integration_id = body.get("integration_id", "")
    channel_id = body.get("channel_id", "")
    sender_id = body.get("sender_id", "")
    text = body.get("text", "")
    kind = body.get("kind", "")
    sender_name = body.get("sender_name", "")
    content = body.get("content")

    instance = _resolve_instance(integrations, integration_id)

    message = await instance.send_to_channel(
        channel_id=channel_id,
        sender_id=sender_id,
        text=text,
        kind=kind,
        sender_name=sender_name,
        content=content,
    )

    return JSONResponse(
        {
            "status": "ok",
            "integration_id": instance.ingress.integration_id,
            "message_id": message.message_id,
            "source": msgspec.to_builtins(message.source),
        },
        status_code=202,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _request_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise ValueError("request body must be valid JSON")
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return cast(dict[str, Any], payload)


def _resolve_instance(
    integrations: IntegrationCore,
    integration_id: str,
) -> TestImIntegration:
    if integration_id:
        instance = integrations.running_instance(integration_id)
        if not isinstance(instance, TestImIntegration):
            raise LookupError(
                f"integration {integration_id!r} is not a test_im integration"
            )
        return instance

    matches: list[TestImIntegration] = []
    for running_id in integrations.running_integration_ids():
        instance = integrations.running_instance(running_id)
        if isinstance(instance, TestImIntegration):
            matches.append(instance)
    if not matches:
        raise LookupError("no running test_im integration")
    if len(matches) > 1:
        raise ValueError(
            "integration_id is required when multiple test_im integrations run"
        )
    return matches[0]
