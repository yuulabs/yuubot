from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, cast

import msgspec
import pytest

from yuubot.chat import harness as harness_module
from yuubot.chat.harness import Harness, HarnessConfig
from yuubot.domain.messages import ConversationContext, ModelCard
from yuubot.domain.stream import ToolCall
from yuubot.tools.base import ToolConfig


class Payload(msgspec.Struct, frozen=True, kw_only=True):
    pass


class SlowPrepareTool:
    payload_type: ClassVar[type[msgspec.Struct]] = Payload

    def __init__(self) -> None:
        self.prepare_started = asyncio.Event()
        self.prepare_released = asyncio.Event()
        self.prepare_cancelled = False
        self.executed = False

    async def prepare(self) -> None:
        self.prepare_started.set()
        try:
            await self.prepare_released.wait()
        except asyncio.CancelledError:
            self.prepare_cancelled = True
            raise

    async def execute(self, payload: msgspec.Struct) -> str:
        del payload
        self.executed = True
        return "ok"

    async def close(self) -> None:
        return None


async def test_harness_starts_prepare_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = SlowPrepareTool()

    def fake_build_tools(configs: dict[str, ToolConfig], context: ConversationContext, runtime: object) -> dict[str, SlowPrepareTool]:
        del configs, context, runtime
        return {"slow": tool}

    monkeypatch.setattr(harness_module, "build_tools", fake_build_tools)
    harness = Harness.from_config(
        HarnessConfig(tools={"slow": ToolConfig(type="slow")}),
        ConversationContext(
            model=ModelCard(selector="test"),
            conversation_id="c1",
            actor="a1",
            workspace=tmp_path,
        ),
        cast(Any, None),
    )

    await tool.prepare_started.wait()
    await harness.close()

    assert tool.prepare_cancelled


async def test_harness_prepare_wait_is_not_part_of_tool_timeout() -> None:
    tool = SlowPrepareTool()
    prepare_task = asyncio.create_task(tool.prepare())
    harness = Harness(tools={"slow": tool}, prepare_tasks={"slow": prepare_task})
    gather_task = asyncio.create_task(
        harness.gather(
            [ToolCall(id="call-1", name="slow", arguments="{}")],
            asyncio.Event(),
            timeout=0.01,
        )
    )

    await tool.prepare_started.wait()
    await asyncio.sleep(0.02)
    assert not gather_task.done()

    tool.prepare_released.set()
    results = await gather_task
    await harness.close()

    assert tool.executed
    assert results[0].content[0].text == "ok"
