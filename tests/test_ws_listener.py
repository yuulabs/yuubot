import asyncio
from typing import cast

import pytest

from yuubot import Yuubot
from yuubot.chat.listener import WsListener
from yuubot.chat.loop import ConversationSnapshot
from yuubot.domain.stream import StreamEvent, TextDeltaPayload
from yuubot.runtime.tasks import RuntimeTaskRecord, TaskRegistry
from yuubot.web.ws import _task_subscribe
from yuubot.web.ws_commands import TaskSubscribePayload


class _Runtime:
    def __init__(self, tasks: TaskRegistry) -> None:
        self.tasks = tasks


class _App:
    def __init__(self, tasks: TaskRegistry) -> None:
        self.runtime = _Runtime(tasks)


@pytest.mark.asyncio
async def test_conversation_frames_preserve_snapshot_delta_commit_order() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener._ensure_conversation_worker()
    await listener.on_snapshot("conv-1", ConversationSnapshot([], [], 0))
    chunk = StreamEvent("text-0", "text_delta", TextDeltaPayload("hi"))
    await listener.on_delta("conv-1", chunk, 1)
    await listener.on_commit("conv-1", [], False, 2)
    await asyncio.sleep(0)

    assert [frame["type"] for frame in sent] == [
        "conversation.snapshot",
        "conversation.delta",
        "conversation.commit",
    ]
    assert cast(dict[str, object], sent[1]["payload"])["version"] == 1
    await listener.close()


@pytest.mark.asyncio
async def test_conversation_queue_overflow_retains_newest_version_for_gap_recovery() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    chunk = StreamEvent("text-0", "text_delta", TextDeltaPayload("x"))
    for version in range(1, 301):
        await listener.on_delta("conv-1", chunk, version)
    listener._ensure_conversation_worker()
    await asyncio.sleep(0.01)

    versions = [cast(dict[str, object], frame["payload"])["version"] for frame in sent]
    assert versions[-1] == 300
    assert versions[0] > 1
    await listener.close()


@pytest.mark.asyncio
async def test_task_subscribe_terminal_frame_includes_stdout_written_before_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    tasks = TaskRegistry()
    record = RuntimeTaskRecord("task-1", "actor:amy:conv:c1", "shell", status="running")
    tasks.put(record)
    listener = WsListener(send)

    def complete_before_stdout_subscription(
        self: WsListener,
        task_id: str,
        stdout: object,
        status: str,
    ) -> None:
        del self, stdout, task_id, status
        record.stdout.write("ready\n")
        record.status = "done"
        record.mark_terminal()

    monkeypatch.setattr(WsListener, "start_task_stdout", complete_before_stdout_subscription)
    await _task_subscribe(cast(Yuubot, _App(tasks)), send, listener, "cmd-1", TaskSubscribePayload(record.id))

    task_events = [frame for frame in sent if frame.get("type") == "task.event"]
    assert task_events[-1]["payload"] == {"task_id": record.id, "status": "done", "stdout": "ready\n"}
