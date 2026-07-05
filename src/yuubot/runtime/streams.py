"""Incremental text output streams for runtime tasks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from attrs import define, field

TaskCoroFactory = Callable[["TextStream", "TextStream"], Awaitable[object]]


@define
class TextStream:
    chunks: list[str] = field(factory=list)
    _subscribers: set[asyncio.Queue[str]] = field(factory=set)

    def write(self, text: str) -> None:
        self.chunks.append(text)
        for subscriber in list(self._subscribers):
            subscriber.put_nowait(text)

    def tail(self, *, max_bytes: int) -> str:
        encoded = b"".join(chunk.encode() for chunk in self.chunks)
        if len(encoded) <= max_bytes:
            return "".join(self.chunks)
        return encoded[-max_bytes:].decode("utf-8", errors="replace")

    async def subscribe(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
