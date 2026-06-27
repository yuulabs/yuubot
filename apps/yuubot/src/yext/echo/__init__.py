"""Hand-written echo facade for actor Python sessions.

This is a test-only facade that mirrors the ``yext.github`` RPC pattern. It
exists because Phase 5.1 removed the mechanical codegen-based ``yext`` package
in favour of hand-written facades, leaving the ``echo.echo`` capability used by
runtime plumbing tests without a callable surface.

The round-trip is:

    ipykernel session -> `echo()` -> `_invoke("echo.echo", payload)`
        -> TCP bridge -> IntegrationInvokeBridge -> integrations.invoke(...)
        -> EchoIntegration.invoke_echo() returns EchoPayload unchanged.

The bridge converts the output struct with ``struct_to_dict(..., omit_defaults=True)``,
so an ``EchoPayload(value="hello echo")`` round-trips back as
``{"value": "hello echo"}`` exactly.
"""

from __future__ import annotations

from yuubot.core.facade.protocol import FacadeRpcRequest
from yb import _context
from yb._client import request as _request


async def echo(value: object = None, *, integration_id: str = "") -> dict[str, object]:
    """Echo ``value`` through the integration facade RPC bridge.

    Returns the daemon result dict (the EchoPayload fields as a JSON object).
    """

    payload_value = str(value) if value is not None else ""
    return await _invoke(
        "echo.echo",
        {"value": payload_value},
        integration_id=integration_id,
    )


async def _invoke(
    capability_id: str,
    payload: dict[str, object],
    *,
    integration_id: str = "",
) -> dict[str, object]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    response = await _request(
        FacadeRpcRequest(
            token=bridge.token,
            actor_id=actor.actor_id,
            integration_id=integration_id,
            agent_name=actor.agent_name,
            session_id=actor.session_id,
            mailbox_id=actor.mailbox_id,
            capability_id=capability_id,
            payload=payload,
        )
    )
    result = response.result
    if not isinstance(result, dict):
        raise TypeError("echo facade result must be a JSON object")
    return result


__all__ = ["echo"]
