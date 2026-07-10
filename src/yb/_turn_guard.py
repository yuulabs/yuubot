"""Fast worker-side guard; Runtime remains the authoritative limit owner."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

_LIMITS = {"fixer_gemini": 1, "fixer_grok": 1, "web_search": 3}
_token = ""
_successes: dict[str, int] = {}
_in_flight: dict[str, int] = {}
_lock = asyncio.Lock()


def configure(token: str) -> None:
    global _token
    if token == _token:
        return
    _token = token
    _successes.clear()
    _in_flight.clear()
    os.environ["YUUBOT_TURN_TOKEN"] = token


async def run(capability: str, operation: Callable[[], Awaitable[T]]) -> T:
    if not _token:
        return await operation()
    limit = _LIMITS[capability]
    async with _lock:
        if _successes.get(capability, 0) + _in_flight.get(capability, 0) >= limit:
            raise RuntimeError(_limit_message(capability))
        _in_flight[capability] = _in_flight.get(capability, 0) + 1
    try:
        result = await operation()
    except BaseException:
        async with _lock:
            _release(_in_flight, capability)
        raise
    async with _lock:
        _release(_in_flight, capability)
        _successes[capability] = _successes.get(capability, 0) + 1
    return result


def _release(counts: dict[str, int], capability: str) -> None:
    remaining = counts.get(capability, 0) - 1
    if remaining > 0:
        counts[capability] = remaining
    else:
        counts.pop(capability, None)


def _limit_message(capability: str) -> str:
    if capability == "web_search":
        return "search_limit_reached: This turn has already used yext.web.search 3 times. Combine the remaining questions into one request, or use yb.fixer.ask_gemini/ask_grok if available."
    return f"fixer_limit_reached: This turn has already used {capability.replace('_', '.')} once."
