import pytest

from yuubot import Yuubot
from yuubot.chat.listener import WsListener
from yuubot.runtime.tasks import RuntimeTaskRecord, TaskRegistry
from yuubot.web.ws import _task_subscribe
from yuubot.web.ws_commands import TaskSubscribePayload
from typing import cast


class _Runtime:
    def __init__(self, tasks: TaskRegistry) -> None:
        self.tasks = tasks


class _App:
    def __init__(self, tasks: TaskRegistry) -> None:
        self.runtime = _Runtime(tasks)


@pytest.mark.asyncio
async def test_track_send_replaces_existing_conversation_track() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_send("cmd1", "conv-1")
    listener.track_send("cmd2", "conv-1")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "hi"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert stream_frames[0]["id"] == "cmd2"


@pytest.mark.asyncio
async def test_task_subscribe_terminal_frame_includes_stdout_written_before_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    tasks = TaskRegistry()
    record = RuntimeTaskRecord(id="task-1", owner="actor:amy:conv:c1", kind="shell", status="running")
    tasks.put(record)
    listener = WsListener(send)

    def complete_before_stdout_subscription(
        self: WsListener,
        task_id: str,
        stdout: object,
        status: str,
    ) -> None:
        del self, stdout
        del task_id, status
        record.stdout.write("ready\n")
        record.status = "done"
        record.mark_terminal()

    monkeypatch.setattr(WsListener, "start_task_stdout", complete_before_stdout_subscription)

    await _task_subscribe(cast(Yuubot, _App(tasks)), send, listener, "cmd-1", TaskSubscribePayload(task_id=record.id))

    task_events = [frame for frame in sent if frame.get("type") == "task.event"]
    assert task_events[-1]["payload"] == {"task_id": record.id, "status": "done", "stdout": "ready\n"}


@pytest.mark.asyncio
async def test_track_send_keeps_different_conversations() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_send("cmd1", "conv-1")
    listener.track_send("cmd2", "conv-2")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "a"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert stream_frames[0]["id"] == "cmd1"


@pytest.mark.asyncio
async def test_history_subscriber_receives_stream_without_track_send() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_history("conv-1")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "hi"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert "id" not in stream_frames[0]


@pytest.mark.asyncio
async def test_track_send_prevents_duplicate_history_stream() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_history("conv-1")
    listener.track_send("cmd1", "conv-1")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "hi"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert stream_frames[0]["id"] == "cmd1"
