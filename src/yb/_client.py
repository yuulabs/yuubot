"""Shared local bridge client for handwritten yb facade modules."""

from __future__ import annotations

import asyncio

import msgspec

from yuubot.core.facade.protocol import FacadeRpcRequest, FacadeRpcResponse
from yb import _context


async def request(request: FacadeRpcRequest) -> FacadeRpcResponse:
    bridge = _context.bridge_context()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(bridge.host, bridge.port),
        timeout=bridge.timeout_s,
    )
    try:
        writer.write(msgspec.json.encode(request) + b"\n")
        await writer.drain()
        raw_response = await asyncio.wait_for(
            reader.readline(),
            timeout=bridge.timeout_s,
        )
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw_response:
        raise RuntimeError("facade bridge returned no response")
    response = msgspec.json.decode(raw_response, type=FacadeRpcResponse)
    if not response.ok:
        error = response.error
        error_type = error.type if error else "RuntimeError"
        message = error.message if error else "facade bridge call failed"
        raise RuntimeError(f"{error_type}: {message}")
    return response