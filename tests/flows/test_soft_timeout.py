"""Flow: slow tool completes and agent produces final response."""

from __future__ import annotations

import asyncio
import json

import yuutools as yt

from tests.conftest import MASTER_QQ, make_group_event
from tests.helpers import history_text
from tests.mocks import make_text_response, make_tool_call_response, mock_llm, mock_recorder_api


async def _wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)


def _install_slow_progress_tool(monkeypatch):
    """Expose a long-running tool through the public tool registry."""
    from yuuagents import tools as agent_tools

    @yt.tool(name="slow_progress", description="Emit progress and keep running.")
    async def slow_progress() -> str:
        await asyncio.sleep(0.2)
        return "slow tool finished"

    original_get = agent_tools.get

    def patched_get(names):
        resolved = [name for name in names if name != "slow_progress"]
        tools = list(original_get(resolved))
        if "slow_progress" in names:
            tools.append(slow_progress)
        return tools

    monkeypatch.setattr(agent_tools, "get", patched_get)


async def test_slow_tool_completes_and_agent_responds(dispatcher, session_mgr, monkeypatch):
    """A slow tool should complete normally and the agent should produce a final response."""
    _install_slow_progress_tool(monkeypatch)
    from yuubot.characters import register
    from yuubot.prompt import AgentSpec, Character
    register(Character(
        name="main",
        description="Test main agent",
        min_role="folk",
        persona="你是测试机器人。",
        spec=AgentSpec(
            tools=["slow_progress"],
            max_steps=4,
        ),
        provider="test",
        model="test-model",
    ))

    responses = [
        make_tool_call_response("slow_progress", json.dumps({}), "call_slow"),
        make_text_response("任务完成"),
    ]

    with mock_recorder_api(), mock_llm(responses):
        await dispatcher.dispatch(make_group_event("/yllm 开始慢任务", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000", timeout=8.0)

    session = session_mgr.get(1)
    assert session is not None
    text = history_text(session.history)
    assert "slow tool finished" in text
    assert "任务完成" in text
