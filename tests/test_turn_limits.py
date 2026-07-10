from __future__ import annotations

import asyncio

import pytest

from yuubot.runtime.turn_limits import TurnIdentity, TurnLimitError, TurnLimitRegistry


def _registry() -> tuple[TurnLimitRegistry, str]:
    registry = TurnLimitRegistry()
    token = registry.open(TurnIdentity("amy", "c1", "turn-1", "trace-1"))
    return registry, token


@pytest.mark.asyncio
async def test_fixer_failures_release_the_success_allowance() -> None:
    registry, token = _registry()

    async def fail() -> str:
        raise RuntimeError("gateway unreachable")

    with pytest.raises(RuntimeError, match="gateway unreachable"):
        await registry.run(token, "fixer_gemini", fail)

    assert await registry.run(token, "fixer_gemini", lambda: asyncio.sleep(0, result="answer")) == "answer"
    with pytest.raises(TurnLimitError) as exc_info:
        await registry.run(token, "fixer_gemini", lambda: asyncio.sleep(0, result="extra"))
    assert exc_info.value.code == "fixer_limit_reached"


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_exceed_limit() -> None:
    registry, token = _registry()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pending() -> str:
        entered.set()
        await release.wait()
        return "answer"

    first = asyncio.create_task(registry.run(token, "fixer_grok", pending))
    await entered.wait()
    with pytest.raises(TurnLimitError) as exc_info:
        await registry.run(token, "fixer_grok", lambda: asyncio.sleep(0, result="extra"))
    assert exc_info.value.code == "fixer_limit_reached"
    release.set()
    assert await first == "answer"


@pytest.mark.asyncio
async def test_web_search_allows_three_successes_and_new_turn_resets() -> None:
    registry, token = _registry()
    for index in range(3):
        assert await registry.run(token, "web_search", lambda: asyncio.sleep(0, result=index)) == index
    with pytest.raises(TurnLimitError) as exc_info:
        await registry.run(token, "web_search", lambda: asyncio.sleep(0, result=4))
    assert exc_info.value.code == "search_limit_reached"

    registry.close(token)
    next_token = registry.open(TurnIdentity("amy", "c1", "turn-2", "trace-1"))
    assert await registry.run(next_token, "web_search", lambda: asyncio.sleep(0, result="fresh")) == "fresh"
