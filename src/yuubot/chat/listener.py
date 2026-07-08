"""Per-WebSocket connection listener that filters runtime events into WS frames."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import msgspec
from attrs import define, field

from .history import PREFIX_KINDS
from ..domain.stream import StreamEvent
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
    _send_tracks: list[tuple[object, str]] = field(factory=list)
    _task_id: str | None = None
    _task_status: str = ""
    _task_stdout_task: asyncio.Task[None] | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def track_events(self, kinds: set[str]) -> None:
        self._event_kinds = kinds
        self._track_all_events = not kinds

    def track_history(self, conversation_id: str) -> None:
        self._history_conversation_id = conversation_id

    def track_send(self, command_id: object, conversation_id: str) -> None:
        self._send_tracks = [(cid, cid_) for cid, cid_ in self._send_tracks if cid_ != conversation_id]
        self._send_tracks.append((command_id, conversation_id))

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

        sent_via_track = False
        for command_id, tracked_conversation_id in self._send_tracks:
            if conversation_id != tracked_conversation_id:
                continue
            await self._send_conversation_frame(kind, command_id, payload)
            sent_via_track = True

        if (
            not sent_via_track
            and self._history_conversation_id is not None
            and conversation_id == self._history_conversation_id
        ):
            await self._send_conversation_frame(kind, None, payload)

    async def _send_conversation_frame(
        self,
        kind: str,
        command_id: object | None,
        payload: object,
    ) -> None:
        frame: dict[str, object] = {}
        if command_id is not None:
            frame["id"] = command_id
        if isinstance(payload, ConversationStreamPayload):
            frame["type"] = "conversation.stream"
            frame["payload"] = {
                "conversation_id": payload.conversation_id,
                "event": _wire_value(payload.event),
            }
        elif isinstance(payload, ConversationOutputPayload):
            frame["type"] = "conversation.output"
            frame["payload"] = {
                "conversation_id": payload.conversation_id,
                "reason": payload.reason,
            }
        elif isinstance(payload, ConversationToolResultsPayload):
            frame["type"] = "conversation.tool_results"
            frame["payload"] = {
                "conversation_id": payload.conversation_id,
                "count": payload.count,
                "results": payload.results,
            }
        elif isinstance(payload, ConversationToolProgressPayload):
            frame["type"] = "conversation.tool_progress"
            frame["payload"] = {
                "conversation_id": payload.conversation_id,
                "tool_call_id": payload.tool_call_id,
                "tool_name": payload.tool_name,
                "text": payload.text or None,
                "task": payload.task or None,
            }
        else:
            return
        await self._send(frame)

    async def _stdout_loop(self, task_id: str, stdout: object, status: str) -> None:
        from ..runtime.streams import TextStream

        if not isinstance(stdout, TextStream):
            return
        try:
            async for text in stdout.subscribe():
                if self._closed:
                    break
                await self._send(
                    {"type": "task.event", "payload": {"task_id": task_id, "status": status, "stdout": text}}
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
