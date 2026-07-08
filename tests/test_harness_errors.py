from __future__ import annotations

import asyncio
from typing import ClassVar

import msgspec

from yuubot.chat.harness import Harness
from yuubot.domain.messages import ContentItem
from yuubot.domain.stream import StreamEvent, ToolCall
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


def _noop_emit(*_args: object, **_kwargs: object) -> None:
    return None


async def test_harness_emits_tool_result_end_for_final_result() -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def emit(kind: str, **payload: object) -> None:
        emitted.append((kind, payload))

    harness = Harness(tools={}, emit=emit, conversation_id="c1")
    results = await harness.gather(
        [ToolCall(id="call-1", name="missing", arguments="{}")],
        asyncio.Event(),
    )

    assert results[0].content[0].text == "unknown tool: missing"
    assert emitted[0][0] == "conversation.stream"
    assert emitted[0][1]["conversation_id"] == "c1"
    event = emitted[0][1]["event"]
    assert isinstance(event, StreamEvent)
    assert event.group_id == "call-1"
    assert event.kind == "tool_result_end"
    assert event.payload == {
        "tool_call_id": "call-1",
        "tool_name": "missing",
        "content": [
            {
                "kind": "text",
                "text": "unknown tool: missing",
                "path": "",
                "url": "",
                "mime": "text/plain",
                "meta": {},
            }
        ],
    }


async def test_harness_binds_progress_before_starting_tool_task() -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def emit(kind: str, **payload: object) -> None:
        emitted.append((kind, payload))

    harness = Harness(tools={"progress": ProgressTool()}, emit=emit, conversation_id="c1")
    results = await harness.gather(
        [ToolCall(id="call-1", name="progress", arguments="{}")],
        asyncio.Event(),
    )

    assert results[0].content[0].text == "done"
    stream_events: list[StreamEvent] = []
    for kind, payload in emitted:
        event = payload.get("event")
        if kind == "conversation.stream" and isinstance(event, StreamEvent):
            stream_events.append(event)
    assert [event.kind for event in stream_events] == ["tool_result_delta", "tool_result_end"]


async def test_harness_tool_errors_include_exception_type_and_cause() -> None:
    harness = Harness(tools={"execute_python": BrokenTool()}, emit=_noop_emit, conversation_id="c1")
    results = await harness.gather(
        [ToolCall(id="call-1", name="execute_python", arguments="{}")],
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
        return [ContentItem(kind="text", text="token=sk-fake1234567890abcdef")]

    async def close(self) -> None:
        return None


async def test_harness_redacts_secret_patterns_in_tool_results() -> None:
    harness = Harness(
        tools={"leaky": LeakyTool(), "leaky_list": LeakyListTool()},
        emit=_noop_emit,
        conversation_id="c1",
    )
    results = await harness.gather(
        [
            ToolCall(id="call-1", name="leaky", arguments="{}"),
            ToolCall(id="call-2", name="leaky_list", arguments="{}"),
        ],
        asyncio.Event(),
    )

    assert REDACTED in results[0].content[0].text
    assert "sk-fake" not in results[0].content[0].text
    assert REDACTED in results[1].content[0].text
    assert "sk-fake" not in results[1].content[0].text
