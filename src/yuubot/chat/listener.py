"""Per-WebSocket connection listener that filters runtime events into WS frames."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import msgspec
from attrs import define, field

from .history import PREFIX_KINDS
from ..domain.stream import StreamEvent

WSCommandSend = Callable[[dict[str, object]], Awaitable[None]]

_STREAM_KINDS = frozenset({"conversation.stream", "conversation.output", "conversation.tool_results"})


def _wire_value(value: object) -> object:
    if isinstance(value, StreamEvent):
        return msgspec.to_builtins(value)
    return value


def _wire_event_payload(payload: dict[str, object]) -> dict[str, object]:
    if "event" not in payload:
        return payload
    wired = dict(payload)
    wired["event"] = _wire_value(payload["event"])
    return wired


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

    async def on_event(self, kind: str, payload: dict[str, object]) -> None:
        if self._closed:
            return
        if self._track_all_events or (self._event_kinds and kind in self._event_kinds):
            await self._send({"type": "runtime.event", "payload": {"kind": kind, "event": _wire_event_payload(payload)}})
        if self._history_conversation_id is not None and kind == "conversation.history.append":
            if payload.get("conversation_id") != self._history_conversation_id:
                return
            item = payload.get("item")
            if isinstance(item, dict) and str(item.get("kind")) in PREFIX_KINDS:
                return
            await self._send({"type": "conversation.history.append", "payload": payload})

        if kind not in _STREAM_KINDS:
            return

        conversation_id = payload.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            return

        sent_via_track = False
        for command_id, tracked_conversation_id in self._send_tracks:
            if conversation_id != tracked_conversation_id:
                continue
            await self._send_conversation_frame(kind, command_id, conversation_id, payload)
            sent_via_track = True

        if (
            not sent_via_track
            and self._history_conversation_id is not None
            and conversation_id == self._history_conversation_id
        ):
            await self._send_conversation_frame(kind, None, conversation_id, payload)

    async def _send_conversation_frame(
        self,
        kind: str,
        command_id: object | None,
        conversation_id: str,
        payload: dict[str, object],
    ) -> None:
        frame: dict[str, object] = {}
        if command_id is not None:
            frame["id"] = command_id
        if kind == "conversation.stream":
            frame["type"] = "conversation.stream"
            frame["payload"] = {
                "conversation_id": conversation_id,
                "event": _wire_value(payload.get("event")),
            }
        elif kind == "conversation.output":
            frame["type"] = "conversation.output"
            frame["payload"] = {
                "conversation_id": conversation_id,
                "reason": payload.get("reason"),
            }
        elif kind == "conversation.tool_results":
            frame["type"] = "conversation.tool_results"
            frame["payload"] = {
                "conversation_id": conversation_id,
                "count": payload.get("count"),
                "results": payload.get("results", []),
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

    async def send_task_terminal(self, task_id: str, status: str) -> None:
        if self._closed:
            return
        await self._send({"type": "task.event", "payload": {"task_id": task_id, "status": status, "stdout": ""}})
