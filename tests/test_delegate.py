from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from yuubot.app import Yuubot
from yuubot.domain import (
    ConversationContext,
    LLMInput,
    StreamEvent,
    StreamStopPayload,
    TextDeltaPayload,
    Usage,
)
from yuubot.runtime.cache import CachePool
from yuubot.runtime.subagents import SUBAGENTS
from yuubot.runtime.turn_limits import TurnIdentity
from yuubot.tools.delegate import DESCRIPTION, DelegatePayload, DelegateTool
from yuubot.tools.registry import all_tool_specs


class RecordingDelegateStream:
    def __init__(self, release: asyncio.Event | None = None) -> None:
        self.calls: list[tuple[LLMInput, str, ConversationContext, dict[str, str] | None]] = []
        self.release = release

    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del cache, stop_event
        self.calls.append((input, model, context, metadata))
        if self.release is not None:
            await self.release.wait()
        yield StreamEvent("text", "text_delta", TextDeltaPayload("subagent result"))
        yield StreamEvent("stop", "stream_stop", StreamStopPayload("stop", Usage(3, 0, 0, 2), {"model": str(model)}))

    async def close(self) -> None:
        return None


def _context(tmp_path: Path, token: str, depth: int = 0) -> ConversationContext:
    return ConversationContext(
        "parent-model",
        "parent-c1",
        "amy",
        tmp_path,
        rpc={"turn_token": token, "turn_id": "turn-1", "delegation_depth": depth},
    )


async def _app_and_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: RecordingDelegateStream):
    app = await Yuubot.create(tmp_path / "data")
    app.runtime.gateway_client = stream
    token = app.runtime.turn_limits.open(TurnIdentity("amy", "parent-c1", "turn-1", "trace-1"))
    context = _context(tmp_path / "workspace", token)
    context.workspace.mkdir()
    monkeypatch.setattr(
        "yuubot.tools.registry.all_tool_configs",
        lambda: {"delegate": __import__("yuubot.tools.registry", fromlist=["all_tool_configs"]).ToolConfig("delegate")},
    )
    return app, token, DelegateTool(context, app.runtime)


def test_delegate_tool_spec_is_complete_and_schema_is_closed() -> None:
    spec = next(item["function"] for item in all_tool_specs() if item["function"]["name"] == "delegate")
    description = str(spec["description"])
    for phrase in (
        "return its Runtime Task id immediately",
        "Up to four",
        "explore:",
        "web-scout:",
        "reviewer:",
        "same:",
        "fast:",
        "intelligent:",
        "receives only `message`",
        "with delegate removed",
    ):
        assert phrase in description
    parameters = spec["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {"subagent", "model_tier", "message"}
    assert "endpoint" not in DESCRIPTION
    assert "persona" not in parameters["properties"]


@pytest.mark.asyncio
async def test_delegate_runs_with_isolated_prompt_tools_and_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = RecordingDelegateStream()
    app, token, tool = await _app_and_tool(tmp_path, monkeypatch, stream)
    try:
        wire = json.loads(await tool.execute(DelegatePayload("reviewer", "same", "Review src/ only.")))
        record = app.runtime.tasks.get(wire["task_id"])
        await record.wait_terminal()

        assert record.kind == "agent"
        assert record.status == "done"
        assert record.result == "subagent result"
        assert record.metadata["parent_conversation_id"] == "parent-c1"
        assert record.metadata["subagent"] == "reviewer"
        assert record.metadata["model_selector"] == "parent-model"
        assert record.metadata["usage"]
        llm_input, model, child_context, metadata = stream.calls[0]
        assert model == "parent-model"
        assert child_context.conversation_id == f"subagent:{record.id}"
        assert child_context.rpc["delegation_depth"] == 1
        assert not any(spec["function"]["name"] == "delegate" for spec in llm_input.tool_specs)
        prompt = str(llm_input.messages[0].content[0].text)
        assert SUBAGENTS["reviewer"].persona in prompt
        assert "Review src/ only." not in prompt
        assert llm_input.messages[-1].content[0].text == "Review src/ only."
        assert metadata is not None
        assert metadata["purpose"] == "delegate"
        assert metadata["task_id"] == record.id
        assert await app.runtime.history.load(child_context.conversation_id) == []
    finally:
        app.runtime.turn_limits.close(token)
        await app.shutdown()


@pytest.mark.asyncio
async def test_delegate_allows_four_registrations_and_rejects_fifth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = asyncio.Event()
    app, token, tool = await _app_and_tool(tmp_path, monkeypatch, RecordingDelegateStream(release))
    task_ids: list[str] = []
    try:
        for index in range(4):
            result = json.loads(await tool.execute(DelegatePayload("explore", "fast", f"Inspect area {index}.")))
            task_ids.append(result["task_id"])
        with pytest.raises(RuntimeError, match="delegate_limit_reached"):
            await tool.execute(DelegatePayload("explore", "fast", "Inspect one more area."))
    finally:
        release.set()
        for task_id in task_ids:
            await app.runtime.tasks.get(task_id).wait_terminal()
        app.runtime.turn_limits.close(token)
        await app.shutdown()


@pytest.mark.asyncio
async def test_delegate_runtime_depth_guard_rejects_recursive_context(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    token = app.runtime.turn_limits.open(TurnIdentity("amy", "c1", "turn-1", "trace-1"))
    try:
        tool = DelegateTool(_context(tmp_path, token, depth=1), app.runtime)
        with pytest.raises(RuntimeError, match="recursive_delegation_forbidden"):
            await tool.execute(DelegatePayload("reviewer", "same", "Review this."))
    finally:
        app.runtime.turn_limits.close(token)
        await app.shutdown()
