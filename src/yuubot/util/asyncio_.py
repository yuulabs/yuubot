import asyncio
import logging
from collections.abc import Awaitable, Callable

from attrs import define, field

_log = logging.getLogger(__name__)


@define
class BackgroundSweeper:
    _cleanup_task: asyncio.Task[None] | None = field(default=None, init=False)

    async def start(self, interval_s: float, sweep: Callable[[], Awaitable[None]]) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._loop(interval_s, sweep))

    async def stop(self) -> None:
        if self._cleanup_task is None:
            return
        self._cleanup_task.cancel()
        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass
        self._cleanup_task = None

    async def _loop(self, interval_s: float, sweep: Callable[[], Awaitable[None]]) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("background sweep failed")
