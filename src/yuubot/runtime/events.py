"""Process-wide event bus and listener dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Protocol

import msgspec
from attrs import define, field

EVENT_BUFFER_SIZE = 100
NOISY_STREAM_KINDS = frozenset({"text_delta", "reasoning_delta", "tool_arguments_delta"})

_log = logging.getLogger(__name__)


class RuntimeEvent(msgspec.Struct, frozen=True):
    kind: str
    payload: dict[str, object]
    ts: str


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@define
class EventBus:
    """Single event outlet. Keeps a bounded buffer of recent events for snapshots."""

    _buffer: deque[RuntimeEvent] = field(factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE))
    _queue: asyncio.Queue[RuntimeEvent] = field(factory=asyncio.Queue)

    def emit(self, event_kind: str, **payload: object) -> None:
        event = RuntimeEvent(kind=event_kind, payload=dict(payload), ts=_utc_now_iso())
        if _should_buffer_event(event):
            self._buffer.append(event)
        self._queue.put_nowait(event)

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
    stream_event = event.payload.get("event")
    stream_kind = getattr(stream_event, "kind", None)
    if stream_kind is None and isinstance(stream_event, dict):
        stream_kind = stream_event.get("kind")
    return stream_kind not in NOISY_STREAM_KINDS


class Listener(Protocol):
    async def on_event(self, kind: str, payload: dict[str, object]) -> None: ...


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
            event = await self._eventbus.pull()
            await self._dispatch(event)

    async def _dispatch(self, event: RuntimeEvent) -> None:
        for listener in list(self._listeners):
            try:
                await listener.on_event(event.kind, event.payload)
            except Exception:
                _log.exception("listener %r failed on event %s", listener, event.kind)
