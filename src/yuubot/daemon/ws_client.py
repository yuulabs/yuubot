"""WS client — connects to Recorder relay WS to receive events."""

import asyncio
import json
from typing import Callable, Awaitable

import websockets

from loguru import logger


class WSClient:
    """Connects to Recorder internal WS, receives forwarded events."""

    def __init__(self, url: str, on_event: Callable[[dict], Awaitable[None]]) -> None:
        self.url = url
        self.on_event = on_event
        self._task: asyncio.Task | None = None
        self._running = False

    async def connect(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        """Connect with auto-reconnect."""
        while self._running:
            try:
                async with websockets.connect(self.url) as ws:
                    logger.info("Connected to Recorder relay: %s", self.url)
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            await self.on_event(data)
                        except Exception:
                            logger.exception("Error handling event")
            except (ConnectionRefusedError, OSError, websockets.ConnectionClosed) as e:
                logger.warning("Relay connection lost (%s), reconnecting in 3s...", e)
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
