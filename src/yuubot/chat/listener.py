"""Per-WebSocket runtime listener and conversation subscriber."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import msgspec
from attrs import define, field

from .loop import Conversation, ConversationSnapshot
from ..domain.stream import StreamEvent
from ..runtime.events import RuntimeEvent

WSCommandSend = Callable[[dict[str, object]], Awaitable[None]]
CONVERSATION_QUEUE_SIZE = 256


def _wire_event_payload(payload: object) -> dict[str, object]:
    wired = msgspec.to_builtins(payload)
    return wired if isinstance(wired, dict) else {}


@define(eq=False)
class WsListener:
    _send: WSCommandSend
    _event_kinds: set[str] = field(factory=set)
    _track_all_events: bool = field(default=False, init=False)
    _subscriptions: dict[str, Conversation] = field(factory=dict, init=False)
    _conversation_frames: asyncio.Queue[dict[str, object]] = field(
        factory=lambda: asyncio.Queue(maxsize=CONVERSATION_QUEUE_SIZE),
        init=False,
    )
    _conversation_task: asyncio.Task[None] | None = field(default=None, init=False)
    _task_stdout_task: asyncio.Task[None] | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def track_events(self, kinds: set[str]) -> None:
        self._event_kinds = kinds
        self._track_all_events = not kinds

    def has_conversation(self, conversation_id: str) -> bool:
        return conversation_id in self._subscriptions

    async def open_conversation(self, conversation: Conversation) -> None:
        existing = self._subscriptions.get(conversation.id)
        if existing is not None:
            existing.unsubscribe(self)
        self._subscriptions[conversation.id] = conversation
        self._ensure_conversation_worker()
        await conversation.subscribe(self)

    def close_conversation(self, conversation_id: str) -> None:
        conversation = self._subscriptions.pop(conversation_id, None)
        if conversation is not None:
            conversation.unsubscribe(self)

    async def on_snapshot(self, conversation_id: str, snapshot: ConversationSnapshot) -> None:
        self._enqueue_conversation_frame(
            {
                "type": "conversation.snapshot",
                "payload": {
                    "conversation_id": conversation_id,
                    "prefix": snapshot.prefix,
                    "living_chunks": [msgspec.to_builtins(chunk) for chunk in snapshot.living_chunks],
                    "version": snapshot.version,
                },
            },
            reset=True,
        )

    async def on_delta(self, conversation_id: str, chunk: StreamEvent, version: int) -> None:
        self._enqueue_conversation_frame(
            {
                "type": "conversation.delta",
                "payload": {
                    "conversation_id": conversation_id,
                    "chunk": msgspec.to_builtins(chunk),
                    "version": version,
                },
            }
        )

    async def on_commit(
        self,
        conversation_id: str,
        append: list[dict[str, object]],
        continues: bool,
        version: int,
    ) -> None:
        self._enqueue_conversation_frame(
            {
                "type": "conversation.commit",
                "payload": {
                    "conversation_id": conversation_id,
                    "append": append,
                    "continues": continues,
                    "version": version,
                },
            }
        )

    async def on_error(self, conversation_id: str, error: str) -> None:
        self._enqueue_conversation_frame(
            {
                "type": "conversation.error",
                "payload": {"conversation_id": conversation_id, "error": error},
            }
        )

    async def on_event(self, event: RuntimeEvent) -> None:
        if self._closed:
            return
        if self._track_all_events or (self._event_kinds and event.kind in self._event_kinds):
            await self._send(
                {
                    "type": "runtime.event",
                    "payload": {"kind": event.kind, "event": _wire_event_payload(event.payload)},
                }
            )

    def start_task_stdout(self, task_id: str, stdout: object, status: str) -> None:
        from ..runtime.streams import TextStream

        if not isinstance(stdout, TextStream):
            return
        self.stop_task_stdout()
        self._task_stdout_task = asyncio.create_task(self._stdout_loop(task_id, stdout, status))

    def stop_task_stdout(self) -> None:
        if self._task_stdout_task is not None:
            self._task_stdout_task.cancel()
            self._task_stdout_task = None

    async def send_task_terminal(self, task_id: str, status: str, stdout: str = "") -> None:
        if not self._closed:
            await self._send(
                {"type": "task.event", "payload": {"task_id": task_id, "status": status, "stdout": stdout}}
            )

    async def close(self) -> None:
        self._closed = True
        for conversation in list(self._subscriptions.values()):
            conversation.unsubscribe(self)
        self._subscriptions.clear()
        self.stop_task_stdout()
        if self._conversation_task is not None:
            self._conversation_task.cancel()
            await asyncio.gather(self._conversation_task, return_exceptions=True)
            self._conversation_task = None

    def _ensure_conversation_worker(self) -> None:
        if self._conversation_task is None:
            self._conversation_task = asyncio.create_task(self._conversation_frame_loop())

    def _enqueue_conversation_frame(self, frame: dict[str, object], reset: bool = False) -> None:
        if self._closed:
            return
        if reset:
            self._drain_conversation_frames()
        try:
            self._conversation_frames.put_nowait(frame)
        except asyncio.QueueFull:
            # Retain the newest version. The resulting client-side gap triggers a snapshot.
            self._drain_conversation_frames()
            self._conversation_frames.put_nowait(frame)

    def _drain_conversation_frames(self) -> None:
        while True:
            try:
                self._conversation_frames.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _conversation_frame_loop(self) -> None:
        while True:
            frame = await self._conversation_frames.get()
            await self._send(frame)

    async def _stdout_loop(self, task_id: str, stdout: object, status: str) -> None:
        from ..runtime.pty_display import PtyDisplayBuffer
        from ..runtime.streams import TextStream

        if not isinstance(stdout, TextStream):
            return
        buffer = PtyDisplayBuffer()
        last = ""
        async for chunk in stdout.subscribe():
            if self._closed:
                return
            buffer.feed(chunk)
            snapshot = buffer.snapshot()
            if snapshot == last:
                continue
            last = snapshot
            await self._send(
                {"type": "task.event", "payload": {"task_id": task_id, "status": status, "stdout": snapshot}}
            )
