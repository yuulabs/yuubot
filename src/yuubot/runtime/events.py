"""Process-wide event bus and listener dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Protocol

import msgspec
from attrs import define, field

from .event_payloads import (
    ConversationStreamPayload,
    RuntimeEventPayload,
    event_kind,
)

EVENT_BUFFER_SIZE = 100
EVENT_QUEUE_SIZE = 1000
NOISY_STREAM_KINDS = frozenset({"text_delta", "reasoning_delta", "tool_arguments_delta", "tool_result_delta"})

_log = logging.getLogger(__name__)


class RuntimeEvent(msgspec.Struct, frozen=True):
    kind: str
    payload: RuntimeEventPayload
    ts: str
    live_seq: int = 0


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@define
class EventBus:
    """Single event outlet. Keeps a bounded buffer of recent events for snapshots."""

    _buffer: deque[RuntimeEvent] = field(factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE))
    _queue: asyncio.Queue[RuntimeEvent] = field(factory=lambda: asyncio.Queue(maxsize=EVENT_QUEUE_SIZE))

    def emit(self, payload: RuntimeEventPayload, live_seq: int = 0) -> None:
        event = RuntimeEvent(event_kind(payload), payload, _utc_now_iso())
        if live_seq:
            event = RuntimeEvent(event.kind, event.payload, event.ts, live_seq)
        if _should_buffer_event(event):
            self._buffer.append(event)
        try:
            self._queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            if _is_noisy_stream_event(event):
                return
        try:
            self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            _log.warning("runtime event queue remained full after dropping oldest event")

    @property
    def events(self) -> list[RuntimeEvent]:
        return list(self._buffer)

    async def pull(self) -> RuntimeEvent:
        return await self._queue.get()

    def pull_nowait(self) -> RuntimeEvent:
        return self._queue.get_nowait()

    def pending_empty(self) -> bool:
        return self._queue.empty()


def _should_buffer_event(event: RuntimeEvent) -> bool:
    if event.kind == "conversation.tool_progress":
        return False
    if event.kind != "conversation.stream":
        return True
    if not isinstance(event.payload, ConversationStreamPayload):
        return True
    return event.payload.event.kind not in NOISY_STREAM_KINDS


def _is_noisy_stream_event(event: RuntimeEvent) -> bool:
    return (
        event.kind == "conversation.stream"
        and isinstance(event.payload, ConversationStreamPayload)
        and event.payload.event.kind in NOISY_STREAM_KINDS
    )


class Listener(Protocol):
    async def on_event(self, event: RuntimeEvent) -> None: ...


@define
class ListenerHub:
    """Serializes EventBus events and dispatches them to registered listeners."""

    _eventbus: EventBus
    _listeners: list[Listener] = field(factory=list)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)

    def add(self, listener: Listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove(self, listener: Listener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        while not self._eventbus.pending_empty():
            await self._dispatch(self._eventbus.pull_nowait())

    async def _run(self) -> None:
        while True:
            try:
                event = await self._eventbus.pull()
                await self._dispatch(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("listener hub event loop failed")
                await asyncio.sleep(0.1)

    async def _dispatch(self, event: RuntimeEvent) -> None:
        await asyncio.gather(
            *(
                self._dispatch_to_listener(listener, event)
                for listener in list(self._listeners)
            )
        )

    async def _dispatch_to_listener(self, listener: Listener, event: RuntimeEvent) -> None:
        try:
            await listener.on_event(event)
        except Exception:
            _log.exception("listener %r failed on event %s", listener, event.kind)
