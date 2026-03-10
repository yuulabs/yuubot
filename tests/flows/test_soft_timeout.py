"""Flow: soft timeout as a product behavior."""

from __future__ import annotations

import asyncio
import json
import re
import time

import yuutools as yt
import yuullm
from yuuagents import tools as agent_tools

from tests.conftest import MASTER_QQ, make_group_event
from tests.helpers import history_text
from tests.mocks import make_text_response, make_tool_call_response, mock_llm, mock_recorder_api


def _extract_handle(text: str) -> str:
    match = re.search(r"handle=([A-Za-z0-9_]+)", text)
    assert match is not None, text
    return match.group(1)


def _extract_last_handle(text: str) -> str:
    matches = re.findall(r"handle=([A-Za-z0-9_]+)", text)
    assert matches, text
    return matches[-1]


def _usage() -> yuullm.Usage:
    return yuullm.Usage(
        provider="test",
        model="test-model",
        input_tokens=10,
        output_tokens=10,
        total_tokens=20,
    )


async def _wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)


def _install_slow_progress_tool(monkeypatch):
    """Expose a long-running tool through the public tool registry."""

    @yt.tool(name="slow_progress", description="Emit progress and keep running.")
    async def slow_progress(
        current_output_buffer=yt.depends(
            lambda ctx: getattr(ctx, "current_output_buffer", None)
        ),
    ) -> str:
        if current_output_buffer is not None:
            current_output_buffer.write(b"phase 1\n")
        await asyncio.sleep(0.4)
        if current_output_buffer is not None:
            current_output_buffer.write(b"phase 2\n")
        await asyncio.sleep(1.0)
        return "slow tool finished"

    original_get = agent_tools.get

    def patched_get(names):
        resolved = [name for name in names if name != "slow_progress"]
        tools = list(original_get(resolved))
        if "slow_progress" in names:
            tools.append(slow_progress)
        return tools

    monkeypatch.setattr(agent_tools, "get", patched_get)


class _ScriptedProvider:
    def __init__(self, steps):
        self._steps = steps
        self._idx = 0

    async def stream(self, messages, *, model, tools=None, **kw):
        idx = min(self._idx, len(self._steps) - 1)
        self._idx += 1
        items = list(self._steps[idx](str(messages)))

        async def _iter():
            for item in items:
                yield item

        return _iter(), {"usage": _usage(), "cost": None}


def _make_llm_factory(monkeypatch, runner):
    parent_provider = _ScriptedProvider(
        [
            lambda _text: make_tool_call_response(
                "delegate",
                json.dumps(
                    {
                        "agent": "worker",
                        "context": "需要执行一个会持续输出进度的慢任务。",
                        "task": "启动慢任务，然后等待它完成。",
                    },
                    ensure_ascii=False,
                ),
                "call_delegate",
            ),
            lambda _text: make_text_response("parent got delegate handle"),
            # Step 3: after child completes, the loop injects a synthetic
            # CHILD_COMPLETED ping.  The LLM sees the result and responds.
            lambda _text: make_text_response("parent saw child output"),
        ]
    )
    child_provider = _ScriptedProvider(
        [
            lambda _text: make_tool_call_response(
                "slow_progress",
                json.dumps({}),
                "call_slow",
            ),
            lambda text: make_tool_call_response(
                "check_running_tool",
                json.dumps({"handle": _extract_handle(text), "wait": 3}),
                "call_child_check",
            ),
            lambda _text: make_text_response("worker finished"),
        ]
    )

    original_make_llm = runner._make_llm

    def _fake_make_llm(agent_name: str = "main"):
        if agent_name == "worker":
            return yuullm.YLLMClient(
                provider=child_provider,
                default_model="test-model",
                price_calculator=yuullm.PriceCalculator(),
            )
        if agent_name == "main":
            return yuullm.YLLMClient(
                provider=parent_provider,
                default_model="test-model",
                price_calculator=yuullm.PriceCalculator(),
            )
        return original_make_llm(agent_name)

    monkeypatch.setattr(runner, "_make_llm", _fake_make_llm)


async def test_soft_timeout_waits_for_children(dispatcher, session_mgr, monkeypatch):
    """子 flow 未完成时 agent loop 不退出，等子 flow 完成后继续。"""
    _install_slow_progress_tool(monkeypatch)
    from yuubot.characters import register
    from yuubot.prompt import AgentSpec, Character
    register(Character(
        name="main",
        description="Test main agent",
        min_role="folk",
        persona="你是测试机器人。",
        spec=AgentSpec(
            tools=["slow_progress", "check_running_tool"],
            soft_timeout=0.1,
            max_steps=6,
        ),
        provider="test",
        model="test-model",
    ))

    responses = [
        make_tool_call_response("slow_progress", json.dumps({}), "call_slow"),
        # Step 2: LLM sees "still running" handle, emits text.
        # Loop detects running children → waits for child completion ping.
        make_text_response("我先把运行句柄给你"),
        # Step 3: after child completes (CHILD_COMPLETED ping applied),
        # LLM is called again and can acknowledge completion.
        make_text_response("任务完成了"),
    ]

    with mock_recorder_api(), mock_llm(responses):
        await dispatcher.dispatch(make_group_event("/yllm 开始慢任务", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000", timeout=8.0)

    session = session_mgr.get(1)
    assert session is not None
    text = history_text(session.history)
    # The handle was returned during the run
    assert "still running" in text.lower()
    assert "handle=" in text
    # After child completion, the ping was applied as a user message
    # (not as a synthetic check_running_tool)
    assert "任务完成了" in text
    assert "后台通知" in text or "完成" in text
    # The child's completion output should be visible
    assert "slow tool finished" in text


async def test_parent_delegate_can_see_child_tool_output(
    dispatcher, session_mgr, monkeypatch
):
    """delegate 子 flow 完成后，parent 在同一 dispatch 内看到结果。"""
    _install_slow_progress_tool(monkeypatch)
    from yuubot.characters import register
    from yuubot.prompt import AgentSpec, Character
    register(Character(
        name="main",
        description="parent",
        min_role="folk",
        persona="你是测试机器人。",
        spec=AgentSpec(
            tools=["delegate", "check_running_tool"],
            subagents=["worker"],
            soft_timeout=0.1,
            max_steps=8,
        ),
        provider="test",
        model="test-model",
    ))
    register(Character(
        name="worker",
        description="worker",
        min_role="master",
        persona="你是工作代理。",
        spec=AgentSpec(
            tools=["slow_progress", "check_running_tool"],
            soft_timeout=0.1,
            max_steps=6,
        ),
        provider="test",
        model="test-model",
    ))

    _make_llm_factory(monkeypatch, dispatcher.agent_runner)

    with mock_recorder_api():
        await dispatcher.dispatch(make_group_event("/yllm 跑起来", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000", timeout=8.0)

    session = session_mgr.get(1)
    assert session is not None
    text = history_text(session.history)
    # Parent saw the delegate handle during soft timeout
    assert "still running" in text.lower()
    assert "handle=" in text
    # After child completed, parent received the output as a ping notification
    assert "parent saw child output" in text
    assert "后台通知" in text or "完成" in text
    # The delegate's output (worker's last text) should be visible
    assert "worker finished" in text
