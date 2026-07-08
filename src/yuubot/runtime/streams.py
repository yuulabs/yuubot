"""Incremental text output streams for runtime tasks."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from attrs import define, field

TaskCoroFactory = Callable[["TextStream", "TextStream"], Awaitable[object]]

DEFAULT_STREAM_MAX_BYTES = 1024 * 1024
DEFAULT_SUBSCRIBER_QUEUE_SIZE = 1000


@define
class TextStream:
    max_bytes: int = DEFAULT_STREAM_MAX_BYTES
    subscriber_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE
    chunks: list[str] = field(factory=list)
    _subscribers: set[asyncio.Queue[str]] = field(factory=set)
    updated_at: float = field(factory=time.monotonic)
    _wait_event: asyncio.Event = field(factory=asyncio.Event, init=False)

    def write(self, text: str) -> None:
        if not text:
            return
        self.chunks.append(text)
        self._trim()
        self.updated_at = time.monotonic()
        self._wait_event.set()
        for subscriber in list(self._subscribers):
            self._put_subscriber_chunk(subscriber, text)

    def _trim(self) -> None:
        encoded = b"".join(chunk.encode() for chunk in self.chunks)
        if len(encoded) <= self.max_bytes:
            return
        tail = encoded[-self.max_bytes :].decode("utf-8", errors="replace")
        self.chunks = [tail]

    def tail(self, max_bytes: int) -> str:
        encoded = b"".join(chunk.encode() for chunk in self.chunks)
        if len(encoded) <= max_bytes:
            return "".join(self.chunks)
        return encoded[-max_bytes:].decode("utf-8", errors="replace")

    async def subscribe(self, replay: bool = False) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, self.subscriber_queue_size))
        self._subscribers.add(queue)
        if replay:
            for chunk in self.chunks:
                self._put_subscriber_chunk(queue, chunk)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def await_next(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for new output. Returns True when output arrives."""
        event = self._wait_event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        self._wait_event = asyncio.Event()
        return True

    def _put_subscriber_chunk(self, queue: asyncio.Queue[str], chunk: str) -> None:
        try:
            queue.put_nowait(chunk)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass
