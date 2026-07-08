from __future__ import annotations

import pytest

from yuubot.chat.listener import WsListener
from yuubot.domain.stream import StreamEvent
from yuubot.runtime.events import EventBus
from yuubot.tools.progress import bind_progress


def test_eventbus_does_not_buffer_tool_progress() -> None:
    eventbus = EventBus()
    eventbus.emit(
        "conversation.tool_progress",
        conversation_id="c1",
        tool_call_id="call-1",
        tool_name="bash",
        text="hello",
    )
    queued = eventbus.pull_nowait()

    assert queued.kind == "conversation.tool_progress"
    assert eventbus.events == []


def test_eventbus_does_not_buffer_tool_result_delta() -> None:
    eventbus = EventBus()
    eventbus.emit(
        "conversation.stream",
        conversation_id="c1",
        event=StreamEvent(group_id="call-1", kind="tool_result_delta", payload={"text": "chunk"}),
    )
    queued = eventbus.pull_nowait()

    assert queued.kind == "conversation.stream"
    assert eventbus.events == []


def test_bind_progress_emits_tool_progress_events() -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def emit(kind: str, **payload: object) -> None:
        emitted.append((kind, payload))

    progress = bind_progress(
        emit=emit,
        conversation_id="c1",
        tool_call_id="call-1",
        tool_name="bash",
    )
    progress.write("chunk")
    progress.set_task("install")

    assert emitted[0][0] == "conversation.stream"
    assert emitted[0][1]["conversation_id"] == "c1"
    event = emitted[0][1]["event"]
    assert isinstance(event, StreamEvent)
    assert event.group_id == "call-1"
    assert event.kind == "tool_result_delta"
    assert event.payload == {"tool_call_id": "call-1", "tool_name": "bash", "text": "chunk"}
    assert emitted[1:] == [
        (
            "conversation.tool_progress",
            {
                "conversation_id": "c1",
                "tool_call_id": "call-1",
                "tool_name": "bash",
                "text": "chunk",
            },
        ),
        (
            "conversation.tool_progress",
            {
                "conversation_id": "c1",
                "tool_call_id": "call-1",
                "tool_name": "bash",
                "task": "install",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_ws_listener_forwards_tool_progress_frame() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_send("cmd-1", "conv-1")

    await listener.on_event(
        "conversation.tool_progress",
        {
            "conversation_id": "conv-1",
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "text": "building",
        },
    )

    frames = [frame for frame in sent if frame.get("type") == "conversation.tool_progress"]
    assert len(frames) == 1
    assert frames[0]["id"] == "cmd-1"
    assert frames[0]["payload"] == {
        "conversation_id": "conv-1",
        "tool_call_id": "call-1",
        "tool_name": "bash",
        "text": "building",
        "task": None,
    }
