"""Turn-scoped capability context and atomic success limits."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import TypeVar

from attrs import define, field

T = TypeVar("T")

LIMITS = {
    "fixer_gemini": 1,
    "fixer_grok": 1,
    "web_search": 3,
    "delegate": 4,
}


class TurnLimitError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@define(frozen=True)
class TurnIdentity:
    actor_id: str
    conversation_id: str
    turn_id: str
    trace_id: str


@define
class _TurnState:
    identity: TurnIdentity
    successes: dict[str, int] = field(factory=dict)
    in_flight: dict[str, int] = field(factory=dict)
    lock: asyncio.Lock = field(factory=asyncio.Lock)


@define
class TurnLimitRegistry:
    _states: dict[str, _TurnState] = field(factory=dict)

    def open(self, identity: TurnIdentity) -> str:
        token = secrets.token_urlsafe(32)
        self._states[token] = _TurnState(identity)
        return token

    def identity(self, token: str) -> TurnIdentity:
        state = self._states.get(token)
        if state is None:
            raise TurnLimitError("turn_context_invalid", "turn context is missing or has expired")
        return state.identity

    def close(self, token: str) -> None:
        self._states.pop(token, None)

    async def run(self, token: str, capability: str, operation: Callable[[], Awaitable[T]]) -> T:
        state = self._states.get(token)
        if state is None:
            raise TurnLimitError("turn_context_invalid", "turn context is missing or has expired")
        limit = LIMITS[capability]
        async with state.lock:
            used = state.successes.get(capability, 0)
            pending = state.in_flight.get(capability, 0)
            if used + pending >= limit:
                raise TurnLimitError(*_limit_error(capability, limit))
            state.in_flight[capability] = pending + 1
        try:
            result = await operation()
        except BaseException:
            async with state.lock:
                _release(state.in_flight, capability)
            raise
        async with state.lock:
            _release(state.in_flight, capability)
            state.successes[capability] = state.successes.get(capability, 0) + 1
        return result


def _release(counts: dict[str, int], capability: str) -> None:
    remaining = counts.get(capability, 0) - 1
    if remaining > 0:
        counts[capability] = remaining
    else:
        counts.pop(capability, None)


def _limit_error(capability: str, limit: int) -> tuple[str, str]:
    if capability == "web_search":
        return (
            "search_limit_reached",
            "This turn has already used yext.web.search 3 times. Combine the remaining questions into one request, or use yb.fixer.ask_gemini/ask_grok if available.",
        )
    if capability == "delegate":
        return "delegate_limit_reached", "This turn has already created 4 delegate tasks."
    return "fixer_limit_reached", f"This turn has already used {capability.replace('_', '.')} {limit} time."
