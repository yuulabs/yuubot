"""Per-WebSocket connection listener that filters runtime events into WS frames."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import msgspec
from attrs import define, field

from .history import PREFIX_KINDS
from ..domain.stream import StreamEvent, StreamStopPayload
from ..runtime.event_payloads import (
    ConversationHistoryAppendPayload,
    ConversationOutputPayload,
    ConversationStreamPayload,
    ConversationToolProgressPayload,
    ConversationToolResultsPayload,
)
from ..runtime.events import RuntimeEvent

WSCommandSend = Callable[[dict[str, object]], Awaitable[None]]

_STREAM_KINDS = frozenset(
    {"conversation.stream", "conversation.output", "conversation.tool_results", "conversation.tool_progress"}
)


def _wire_value(value: object) -> object:
    if isinstance(value, StreamEvent):
        return msgspec.to_builtins(value)
    return value


def _wire_event_payload(payload: object) -> dict[str, object]:
    wired = msgspec.to_builtins(payload)
    if not isinstance(wired, dict):
        return {}
    if "event" not in wired:
        return wired
    result = dict(wired)
    result["event"] = _wire_value(wired["event"])
    return result


@define
class WsListener:
    _send: WSCommandSend
    _event_kinds: set[str] = field(factory=set)
    _track_all_events: bool = field(default=False, init=False)
    _history_conversation_id: str | None = None
    _send_tracks: list[tuple[object, str, bool]] = field(factory=list)
    _task_id: str | None = None
    _task_status: str = ""
    _task_stdout_task: asyncio.Task[None] | None = field(default=None, init=False)
    _conversation_frame_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    _closed: bool = field(default=False, init=False)

    def track_events(self, kinds: set[str]) -> None:
        self._event_kinds = kinds
        self._track_all_events = not kinds

    def track_history(self, conversation_id: str) -> None:
        self._history_conversation_id = conversation_id

    async def track_history_with_replay(
        self,
        command_id: str | None,
        conversation_id: str,
        replay_payloads: Sequence[object],
        running: bool,
    ) -> None:
        async with self._conversation_frame_lock:
            self.track_history(conversation_id)
            await self._send(
                {
                    "id": command_id,
                    "type": "conversation.history.subscribe.result",
                    "payload": {"conversation_id": conversation_id},
                }
            )
            if running:
                await self._send_live_replay_locked(conversation_id, replay_payloads)

    def track_send(self, command_id: object, conversation_id: str, direct_stream: bool = False) -> None:
        self._send_tracks = [(cid, cid_, direct) for cid, cid_, direct in self._send_tracks if cid_ != conversation_id]
        self._send_tracks.append((command_id, conversation_id, direct_stream))

    def complete_send(self, command_id: object, conversation_id: str) -> None:
        self._send_tracks = [
            (cid, cid_, direct)
            for cid, cid_, direct in self._send_tracks
            if (cid, cid_) != (command_id, conversation_id)
        ]

    def track_task(self, task_id: str, status: str) -> None:
        self._task_id = task_id
        self._task_status = status

    def start_task_stdout(self, task_id: str, stdout: object, status: str) -> None:
        from ..runtime.streams import TextStream

        if not isinstance(stdout, TextStream):
            return
        self._task_id = task_id
        self._task_status = status
        if self._task_stdout_task is not None:
            self._task_stdout_task.cancel()
        self._task_stdout_task = asyncio.create_task(self._stdout_loop(task_id, stdout, status))

    def stop_task_stdout(self) -> None:
        if self._task_stdout_task is not None:
            self._task_stdout_task.cancel()
            self._task_stdout_task = None

    async def on_event(self, event: RuntimeEvent) -> None:
        if self._closed:
            return
        kind = event.kind
        payload = event.payload
        if self._track_all_events or (self._event_kinds and kind in self._event_kinds):
            await self._send(
                {"type": "runtime.event", "payload": {"kind": kind, "event": _wire_event_payload(payload)}}
            )
        if self._history_conversation_id is not None and isinstance(payload, ConversationHistoryAppendPayload):
            async with self._conversation_frame_lock:
                if payload.conversation_id != self._history_conversation_id:
                    return
                item = payload.item
                if str(item.get("kind")) in PREFIX_KINDS:
                    return
                await self._send({"type": "conversation.history.append", "payload": msgspec.to_builtins(payload)})

        if kind not in _STREAM_KINDS:
            return

        conversation_id = _conversation_id(payload)
        if conversation_id is None:
            return

        async with self._conversation_frame_lock:
            await self._send_tracked_conversation_frame(kind, payload, event.live_seq)

    async def _send_tracked_conversation_frame(
        self,
        kind: str,
        payload: object,
        live_seq: int = 0,
    ) -> None:
        conversation_id = _conversation_id(payload)
        if conversation_id is None:
            return

        sent_via_track = False
        completed_tracks: list[tuple[object, str]] = []
        for command_id, tracked_conversation_id, direct_stream in self._send_tracks:
            if conversation_id != tracked_conversation_id:
                continue
            terminal_stream = isinstance(payload, ConversationStreamPayload) and _is_terminal_stream_stop(payload.event)
            if direct_stream:
                if isinstance(payload, ConversationStreamPayload):
                    sent_via_track = True
                    if terminal_stream:
                        completed_tracks.append((command_id, tracked_conversation_id))
                    continue
                if isinstance(payload, ConversationOutputPayload):
                    sent_via_track = True
                    continue
            await self._send_conversation_frame(kind, command_id, payload, live_seq)
            sent_via_track = True
            if terminal_stream:
                completed_tracks.append((command_id, tracked_conversation_id))

        if (
            not sent_via_track
            and self._history_conversation_id is not None
            and conversation_id == self._history_conversation_id
        ):
            await self._send_conversation_frame(kind, None, payload, live_seq)
        for command_id, tracked_conversation_id in completed_tracks:
            self.complete_send(command_id, tracked_conversation_id)

    async def _send_live_replay_locked(
        self,
        conversation_id: str,
        replay_payloads: Sequence[object],
    ) -> None:
        last_live_seq = _last_live_seq(replay_payloads)
        await self._send(
            {
                "type": "conversation.replay.start",
                "payload": {
                    "conversation_id": conversation_id,
                    "last_live_seq": last_live_seq,
                    "count": len(replay_payloads),
                },
            }
        )
        for item in replay_payloads:
            payload = getattr(item, "payload", None)
            live_seq = getattr(item, "seq", 0)
            kind = _payload_kind(payload)
            if kind:
                await self._send_conversation_frame(kind, None, payload, live_seq)
        await self._send(
            {
                "type": "conversation.replay.end",
                "payload": {
                    "conversation_id": conversation_id,
                    "last_live_seq": last_live_seq,
                    "count": len(replay_payloads),
                },
            }
        )

    async def _send_conversation_frame(
        self,
        kind: str,
        command_id: object | None,
        payload: object,
        live_seq: int = 0,
    ) -> None:
        frame: dict[str, object] = {}
        if command_id is not None:
            frame["id"] = command_id
        if isinstance(payload, ConversationStreamPayload):
            frame["type"] = "conversation.stream"
            payload_body: dict[str, object] = {
                "conversation_id": payload.conversation_id,
                "event": _wire_value(payload.event),
            }
            if live_seq:
                payload_body["live_seq"] = live_seq
            frame["payload"] = payload_body
        elif isinstance(payload, ConversationOutputPayload):
            frame["type"] = "conversation.output"
            payload_body: dict[str, object] = {
                "conversation_id": payload.conversation_id,
                "reason": payload.reason,
            }
            if live_seq:
                payload_body["live_seq"] = live_seq
            frame["payload"] = payload_body
        elif isinstance(payload, ConversationToolResultsPayload):
            frame["type"] = "conversation.tool_results"
            payload_body: dict[str, object] = {
                "conversation_id": payload.conversation_id,
                "count": payload.count,
                "results": payload.results,
            }
            if live_seq:
                payload_body["live_seq"] = live_seq
            frame["payload"] = payload_body
        elif isinstance(payload, ConversationToolProgressPayload):
            frame["type"] = "conversation.tool_progress"
            payload_body: dict[str, object] = {
                "conversation_id": payload.conversation_id,
                "tool_call_id": payload.tool_call_id,
                "tool_name": payload.tool_name,
                "text": payload.text or None,
                "task": payload.task or None,
            }
            if live_seq:
                payload_body["live_seq"] = live_seq
            frame["payload"] = payload_body
        else:
            return
        await self._send(frame)

    async def _stdout_loop(self, task_id: str, stdout: object, status: str) -> None:
        from ..runtime.pty_display import PtyDisplayBuffer
        from ..runtime.streams import TextStream

        if not isinstance(stdout, TextStream):
            return
        buffer = PtyDisplayBuffer()
        last = ""
        try:
            async for chunk in stdout.subscribe():
                if self._closed:
                    break
                buffer.feed(chunk)
                snapshot = buffer.snapshot()
                if snapshot == last:
                    continue
                last = snapshot
                await self._send(
                    {"type": "task.event", "payload": {"task_id": task_id, "status": status, "stdout": snapshot}}
                )
        except asyncio.CancelledError:
            raise

    def close(self) -> None:
        self._closed = True
        self.stop_task_stdout()

    async def send_task_terminal(self, task_id: str, status: str, stdout: str = "") -> None:
        if self._closed:
            return
        await self._send({"type": "task.event", "payload": {"task_id": task_id, "status": status, "stdout": stdout}})


def _conversation_id(payload: object) -> str | None:
    conversation_id = getattr(payload, "conversation_id", None)
    return conversation_id if isinstance(conversation_id, str) and conversation_id else None


def _is_terminal_stream_stop(event: StreamEvent) -> bool:
    if event.kind != "stream_stop":
        return False
    payload = event.payload
    return isinstance(payload, StreamStopPayload) and payload.reason not in {"tool_calls", "function_call"}


def _payload_kind(payload: object) -> str:
    if isinstance(payload, ConversationStreamPayload):
        return "conversation.stream"
    if isinstance(payload, ConversationOutputPayload):
        return "conversation.output"
    if isinstance(payload, ConversationToolResultsPayload):
        return "conversation.tool_results"
    if isinstance(payload, ConversationToolProgressPayload):
        return "conversation.tool_progress"
    return ""


def _last_live_seq(replay_payloads: Sequence[object]) -> int:
    last = 0
    for item in replay_payloads:
        seq = getattr(item, "seq", 0)
        if isinstance(seq, int) and seq > last:
            last = seq
    return last
