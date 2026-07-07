from __future__ import annotations

import asyncio
from typing import ClassVar

import msgspec

from yuubot.chat.harness import Harness
from yuubot.domain.messages import ContentItem
from yuubot.domain.stream import ToolCall
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


async def test_harness_tool_errors_include_exception_type_and_cause() -> None:
    harness = Harness(tools={"execute_python": BrokenTool()})
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
    harness = Harness(tools={"leaky": LeakyTool(), "leaky_list": LeakyListTool()})
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
