"""Process-local async event bus with serial dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class Event:
    """Base class for process-local events."""


@dataclass
class QueuedEvent:
    event: Event
    context: Context


@dataclass
class EventSubscription:
    event_types: tuple[type[Event], ...]
    callback: Callable[[Event], Coroutine[Any, Any, None]]


@dataclass
class EventBus:
    """Async in-process event bus with serial dispatch.

    publish() enqueues synchronously (safe from DB transaction context).
    Dispatch runs in a background task, processing events one at a time
    to guarantee ordering and prevent concurrent refresh races.
    """

    _subscriptions: list[EventSubscription] = field(default_factory=list)
    _queue: asyncio.Queue[QueuedEvent] = field(default_factory=asyncio.Queue)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    name: str = "event-bus"

    def subscribe(
        self,
        event_types: list[type[Event]],
        callback: Callable[[Event], Coroutine[Any, Any, None]],
    ) -> None:
        if not event_types:
            raise ValueError("event_types must not be empty")
        for event_type in event_types:
            if event_type is Event or not issubclass(event_type, Event):
                raise TypeError("event_type must be a concrete Event subclass")
        self._subscriptions.append(
            EventSubscription(event_types=tuple(event_types), callback=callback)
        )

    def publish(self, event: Event) -> None:
        """Enqueue an event for async dispatch. Non-blocking, sync-safe."""
        self._queue.put_nowait(QueuedEvent(event=event, context=copy_context()))

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _dispatch_loop(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                await self._dispatch(queued)
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """Process all currently queued events. Useful in tests."""
        if self._task is None:
            while not self._queue.empty():
                queued = await self._queue.get()
                try:
                    await self._dispatch(queued)
                finally:
                    self._queue.task_done()
            return
        await self._queue.join()

    async def _dispatch(self, queued: QueuedEvent) -> None:
        event = queued.event
        for subscription in self._subscriptions:
            if isinstance(event, subscription.event_types):
                try:
                    task = queued.context.run(
                        lambda: asyncio.create_task(subscription.callback(event))
                    )
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "event subscriber failed for %s",
                        type(event).__name__,
                    )
