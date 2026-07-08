import asyncio

import pytest

from yuubot.util.asyncio_ import BackgroundSweeper


@pytest.mark.asyncio
async def test_background_sweeper_continues_after_sweep_error() -> None:
    sweeper = BackgroundSweeper()
    attempts = 0
    recovered = asyncio.Event()

    async def sweep() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        recovered.set()

    await sweeper.start(0.001, sweep)
    try:
        await asyncio.wait_for(recovered.wait(), timeout=0.2)
    finally:
        await sweeper.stop()

    assert attempts >= 2
