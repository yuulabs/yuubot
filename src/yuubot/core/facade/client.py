"""Generated async RPC client template for yext Integration facade functions.

This module holds the source code that gets written into the generated yext
package as ``_client.py``. It lives here as real Python so it can be linted,
type-checked, and tested independently of the code-generation machinery.
"""

from __future__ import annotations

from yuubot.core.facade.context import FACADE_CONTEXT_MODULE

_CLIENT_SOURCE = '''\
"""Generated async RPC client for yext Integration facade functions."""

from __future__ import annotations

from collections.abc import Mapping

import msgspec

import {context_module} as _context
from yuubot.core.facade.protocol import FacadeRpcRequest, FacadeRpcResponse
from yb._client import request as _request


async def invoke(capability_id: str, payload: dict[str, object]) -> dict[str, object]:
    request = FacadeRpcRequest(
        token=_context.TOKEN,
        kind="invoke",
        actor_id=_context.ACTOR_ID,
        capability_id=capability_id,
        payload=payload,
    )
    response = await _request(request)
    result = response.result
    if not isinstance(result, dict):
        raise TypeError("integration facade result must be a JSON object")
    return result


def coerce_payload(value: object, payload: dict[str, object]) -> dict[str, object]:
    if value is None:
        return dict(payload)
    if not payload and isinstance(value, Mapping):
        return dict(value)
    return {{"value": value, **payload}}
'''


def render_client_module(context_module: str = FACADE_CONTEXT_MODULE) -> str:
    """Return the generated _client.py source with the context module name substituted."""
    return _CLIENT_SOURCE.format(context_module=context_module)
