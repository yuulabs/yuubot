from __future__ import annotations

import pytest

from yuubot.chat.listener import WsListener
from yuubot.domain.stream import StreamEvent, ToolResultDeltaPayload
from yuubot.runtime.event_payloads import (
    ConversationStreamPayload,
    ConversationToolProgressPayload,
    RuntimeEventPayload,
)
from yuubot.runtime.events import EventBus
from yuubot.tools.progress import bind_progress

from support.runtime_events import conversation_tool_progress


def test_eventbus_does_not_buffer_tool_progress() -> None:
    eventbus = EventBus()
    eventbus.emit(
        ConversationToolProgressPayload(
            "c1",
            "call-1",
            "bash",
            "hello",
        )
    )
    queued = eventbus.pull_nowait()

    assert queued.kind == "conversation.tool_progress"
    assert eventbus.events == []


def test_eventbus_does_not_buffer_tool_result_delta() -> None:
    eventbus = EventBus()
    eventbus.emit(
        ConversationStreamPayload(
            "c1",
            StreamEvent(
                "call-1",
                "tool_result_delta",
                ToolResultDeltaPayload(text="chunk"),
            ),
        )
    )
    queued = eventbus.pull_nowait()

    assert queued.kind == "conversation.stream"
    assert eventbus.events == []


def test_bind_progress_emits_tool_progress_events() -> None:
    emitted: list[RuntimeEventPayload] = []

    def emit(payload: RuntimeEventPayload) -> None:
        emitted.append(payload)

    progress = bind_progress(
        emit,
        "c1",
        "call-1",
        "bash",
    )
    progress.write("chunk")
    progress.set_task("install")

    assert isinstance(emitted[0], ConversationStreamPayload)
    assert emitted[0].conversation_id == "c1"
    event = emitted[0].event
    assert event.group_id == "call-1"
    assert event.kind == "tool_result_delta"
    assert isinstance(event.payload, ToolResultDeltaPayload)
    assert event.payload.text == "chunk"
    assert emitted[1:] == [
        ConversationToolProgressPayload(
            "c1",
            "call-1",
            "bash",
            "chunk",
        ),
        ConversationToolProgressPayload(
            "c1",
            "call-1",
            "bash",
            task="install",
        ),
    ]


def test_tool_progress_renders_carriage_returns_as_terminal_snapshots() -> None:
    emitted: list[RuntimeEventPayload] = []
    progress = bind_progress(emitted.append, "c1", "call-1", "execute_python")

    progress.write("download 10%\r")
    progress.write("download 90%")

    stream = [
        payload.event
        for payload in emitted
        if isinstance(payload, ConversationStreamPayload)
    ]
    assert isinstance(stream[-1].payload, ToolResultDeltaPayload)
    assert stream[-1].payload.text == "download 90%"
    assert progress.snapshot() == "download 90%"


@pytest.mark.asyncio
async def test_ws_listener_does_not_forward_tool_progress_as_conversation_frame() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)

    await listener.on_event(
        conversation_tool_progress(
            "conv-1",
            "call-1",
            "bash",
            "building",
        )
    )

    assert sent == []
