from __future__ import annotations

import asyncio
from typing import ClassVar

import msgspec

from yuubot.chat.harness import Harness
from yuubot.domain.messages import ContentItem
from yuubot.domain.stream import StreamEvent, ToolCall, ToolResultEndPayload
from yuubot.runtime.event_payloads import ConversationStreamPayload, RuntimeEventPayload
from yuubot.tools.progress import current_progress
from yuubot.util.secrets import REDACTED


class EmptyPayload(msgspec.Struct, frozen=True):
    pass


class BrokenTool:
    payload_type: ClassVar[type[msgspec.Struct]] = EmptyPayload

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        del payload
        try:
            raise ValueError("daemon detail")
        except ValueError as exc:
            raise RuntimeError() from exc

    async def close(self) -> None:
        return None


class ProgressTool:
    payload_type: ClassVar[type[msgspec.Struct]] = EmptyPayload

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        del payload
        progress = current_progress()
        assert progress is not None
        progress.write("live\n")
        return "done"

    async def close(self) -> None:
        return None


class InterruptibleProgressTool:
    payload_type: ClassVar[type[msgspec.Struct]] = EmptyPayload

    def __init__(self, visible: bool = True) -> None:
        self.visible = visible
        self.started = asyncio.Event()
        self.closed = False

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        del payload
        progress = current_progress()
        assert progress is not None
        if self.visible:
            progress.write("step one\nprogress 10%\r")
            progress.write("progress 90%")
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if self.visible:
                progress.write("\nKeyboardInterrupt")
            raise

    async def close(self) -> None:
        self.closed = True


def _noop_emit(_payload: RuntimeEventPayload) -> None:
    return None


async def test_harness_emits_tool_result_end_for_final_result() -> None:
    emitted: list[RuntimeEventPayload] = []

    def emit(payload: RuntimeEventPayload) -> None:
        emitted.append(payload)

    harness = Harness({}, emit, "c1")
    results = await harness.gather(
        [ToolCall("call-1", "missing", "{}")],
        asyncio.Event(),
    )

    assert results[0].content[0].text == "unknown tool: missing"
    assert isinstance(emitted[0], ConversationStreamPayload)
    assert emitted[0].conversation_id == "c1"
    event = emitted[0].event
    assert event.group_id == "call-1"
    assert event.kind == "tool_result_end"
    assert isinstance(event.payload, ToolResultEndPayload)
    assert event.payload.tool_call_id == "call-1"
    assert event.payload.tool_name == "missing"
    assert event.payload.content == [
        {
            "kind": "text",
            "text": "unknown tool: missing",
            "path": "",
            "url": "",
            "mime": "text/plain",
            "meta": {},
        }
    ]


async def test_harness_binds_progress_before_starting_tool_task() -> None:
    emitted: list[RuntimeEventPayload] = []

    def emit(payload: RuntimeEventPayload) -> None:
        emitted.append(payload)

    harness = Harness({"progress": ProgressTool()}, emit, "c1")
    results = await harness.gather(
        [ToolCall("call-1", "progress", "{}")],
        asyncio.Event(),
    )

    assert results[0].content[0].text == "done"
    stream_events: list[StreamEvent] = []
    for payload in emitted:
        if isinstance(payload, ConversationStreamPayload):
            stream_events.append(payload.event)
    assert [event.kind for event in stream_events] == ["tool_result_delta", "tool_result_end"]


async def test_harness_interrupt_persists_terminal_snapshot() -> None:
    tool = InterruptibleProgressTool()
    harness = Harness({"execute_python": tool}, _noop_emit, "c1")
    stop = asyncio.Event()
    gathering = asyncio.create_task(
        harness.gather(
            [ToolCall("call-1", "execute_python", "{}")],
            stop,
        )
    )
    await tool.started.wait()

    stop.set()
    results = await asyncio.wait_for(gathering, timeout=1)

    assert results[0].content[0].text == "step one\nprogress 90%\nKeyboardInterrupt"


async def test_harness_interrupt_without_output_uses_fallback() -> None:
    tool = InterruptibleProgressTool(visible=False)
    harness = Harness({"silent": tool}, _noop_emit, "c1")
    stop = asyncio.Event()
    gathering = asyncio.create_task(
        harness.gather([ToolCall("call-1", "silent", "{}")], stop)
    )
    await tool.started.wait()

    stop.set()
    results = await asyncio.wait_for(gathering, timeout=1)

    assert results[0].content[0].text == "[system] tool call interrupted."


async def test_harness_tool_errors_include_exception_type_and_cause() -> None:
    harness = Harness({"execute_python": BrokenTool()}, _noop_emit, "c1")
    results = await harness.gather(
        [ToolCall("call-1", "execute_python", "{}")],
        asyncio.Event(),
    )

    text = results[0].content[0].text
    assert "execute_python failed: RuntimeError: RuntimeError()" in text
    assert "caused by ValueError: daemon detail" in text


class LeakyTool:
    payload_type: ClassVar[type[msgspec.Struct]] = EmptyPayload

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        del payload
        return "token=sk-fake1234567890abcdef"

    async def close(self) -> None:
        return None


class LeakyListTool:
    payload_type: ClassVar[type[msgspec.Struct]] = EmptyPayload

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> list[ContentItem]:
        del payload
        return [ContentItem("text", "token=sk-fake1234567890abcdef")]

    async def close(self) -> None:
        return None


async def test_harness_redacts_secret_patterns_in_tool_results() -> None:
    harness = Harness(
        {"leaky": LeakyTool(), "leaky_list": LeakyListTool()},
        _noop_emit,
        "c1",
    )
    results = await harness.gather(
        [
            ToolCall("call-1", "leaky", "{}"),
            ToolCall("call-2", "leaky_list", "{}"),
        ],
        asyncio.Event(),
    )

    assert REDACTED in results[0].content[0].text
    assert "sk-fake" not in results[0].content[0].text
    assert REDACTED in results[1].content[0].text
    assert "sk-fake" not in results[1].content[0].text
