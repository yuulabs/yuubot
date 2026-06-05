"""Shared local bridge client for handwritten yb facade modules."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from yb import _context


async def request(payload: dict[str, Any]) -> dict[str, Any]:
    bridge = _context.bridge_context()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(bridge.host, bridge.port),
        timeout=bridge.timeout_s,
    )
    try:
        writer.write(json.dumps(payload, ensure_ascii=True).encode() + b"\n")
        await writer.drain()
        raw_response = await asyncio.wait_for(
            reader.readline(),
            timeout=bridge.timeout_s,
        )
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
    if not raw_response:
        raise RuntimeError("facade bridge returned no response")
    response = json.loads(raw_response.decode())
    if not response.get("ok"):
        error = response.get("error", {})
        error_type = error.get("type", "RuntimeError")
        message = error.get("message", "facade bridge call failed")
        raise RuntimeError(f"{error_type}: {message}")
    return response
