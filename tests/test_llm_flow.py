"""E2E tests for LLM flow — signal queuing, timeout, error steps, agent-fns via real daemon."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
import yuullm
import yuuagents as ya

from yuubot.core.onebot import to_inbound_message
from yuubot.core.models import Context
from yuubot.daemon.agent_runner import AgentRunner
from tests.conftest import (
    make_group_event,
    make_private_event,
)
from tests.framework import (
    ScriptedLLM,
    RecorderMock,
    text,
    tool_call,
    execute_python,
)


# ── Signal queuing: image during running conversation ────────────

@pytest.mark.asyncio
async def test_image_signal_queued_during_running_conversation(db, yuubot_config) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    captured_messages: list[list[yuullm.Message]] = []
    call_count = 0

    async def _fake_stream(self, messages, *, model, tools=None, **kw):
        nonlocal call_count
        del self, model, tools, kw
        call_count += 1
        captured_messages.append(list(messages))
        if call_count == 1:
            started.set()
            await release.wait()
            tt = "没看到图"
        else:
            tt = "看到图了"

        async def _gen() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": tt})

        return _gen(), yuullm.Store(
            usage=yuullm.Usage(provider="test", model="test-model", input_tokens=1, output_tokens=1),
        )

    image_event = make_private_event("", ctx_id=300)
    image_event["message"] = [
        {"type": "image", "data": {
            "url": "https://example.invalid/queued.png",
            "file": "queued.png",
            "local_path": "/tmp/yuubot-queued-image.png",
        }},
    ]
    image_event["raw_message"] = "[CQ:image,file=queued.png]"

    runner = AgentRunner(yuubot_config)

    try:
        with RecorderMock() as recorder:
            with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _fake_stream):
                task = asyncio.create_task(
                    runner.run_conversation(
                        to_inbound_message(make_private_event("图呢？", ctx_id=300)),
                        agent_name="maid",
                        bot_kind="master",
                    ),
                )
                await asyncio.wait_for(started.wait(), timeout=5)

                signal = await runner.render_signal(to_inbound_message(image_event))
                runner.enqueue_signal(list(runner._sessions_by_runtime.values())[0].agent.id, signal)

                release.set()
                await task

        assert call_count >= 2
        assert recorder.texts == ["没看到图", "看到图了"]
    finally:
        release.set()
        await runner.stop()


# ── Timeout ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_timeout_stops_conversation_gracefully(db, yuubot_config) -> None:
    """Agent with very short inactivity timeout should stop with timeout status."""
    import msgspec.structs

    # Create a config with very short timeout
    timeout_config = msgspec.structs.replace(
        yuubot_config,
        daemon=msgspec.structs.replace(yuubot_config.daemon, agent_timeout=0.3),
    )

    async def _slow_stream(self, messages, *, model, tools=None, **kw):
        del self, messages, model, tools, kw
        # Simulate a very slow LLM that takes longer than timeout
        await asyncio.sleep(1.0)

        async def _gen() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": "too late"})

        return _gen(), yuullm.Store(
            usage=yuullm.Usage(provider="test", model="test-model", input_tokens=1, output_tokens=1),
        )

    runner = AgentRunner(timeout_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _slow_stream):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("hello", ctx_id=301)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    assert session.status == "timeout"
    await runner.stop()


# ── LLM error handling ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_stream_error_does_not_crash_runner(db, yuubot_config) -> None:
    async def _error_stream(self, messages, *, model, tools=None, **kw):
        del self, messages, model, tools, kw
        raise RuntimeError("simulated LLM API failure")

    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _error_stream):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("hello", ctx_id=302)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    assert session.status == "error"
    assert "simulated LLM API failure" in session.final_text
    await runner.stop()


# ── execute_python with im.send_message via real daemon ──────────

@pytest.mark.asyncio
async def test_im_send_message_via_execute_python_and_real_daemon(
    db, yuubot_config, test_daemon,
) -> None:
    """Agent calls im.send_message() through execute_python → real daemon → recorder."""
    code = (
        "import im\n"
        "r = await im.send_message('hello from agent!', ctx_id=SESSION_STATE.ctx_id)\n"
        "r\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("message sent"),
    ])
    runner = AgentRunner(yuubot_config)

    with RecorderMock() as recorder:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("send message", ctx_id=303)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ya.ToolStep)]
    assert len(tool_steps) >= 1
    assert "sent" in tool_steps[0].output_text.lower() or "hello from agent" in tool_steps[0].output_text.lower()
    # The send_message call goes through the real daemon which calls recorder API
    assert any("hello from agent!" in seg.get("data", {}).get("text", "")
               for body in recorder.sent for seg in body.get("message", []))
    await runner.stop()


# ── execute_python with im.recent_messages via real daemon ───────

@pytest.mark.asyncio
async def test_im_recent_messages_via_execute_python_and_real_daemon(
    db, yuubot_config, test_daemon,
) -> None:
    """Agent calls im.recent_messages() through execute_python → real daemon → DB."""
    code = (
        "import im\n"
        "msgs = await im.recent_messages(limit=5, ctx_id=SESSION_STATE.ctx_id)\n"
        "f'got {len(msgs)} messages'\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("done"),
    ])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("recent", ctx_id=304)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ya.ToolStep)]
    assert len(tool_steps) >= 1
    assert "got" in tool_steps[0].output_text.lower() or "messages" in tool_steps[0].output_text.lower()
    # Error-free execution
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── execute_python with mem.save/recall via real daemon ──────────

@pytest.mark.asyncio
async def test_mem_save_and_recall_via_execute_python_and_real_daemon(
    db, yuubot_config, test_daemon,
) -> None:
    """Agent saves and recalls memory through execute_python → real daemon → DB."""
    await Context.get_or_create(id=305, defaults={"type": "private", "target_id": 10001})
    code_save = (
        "import mem\n"
        "r = await mem.save_memory('猫最喜欢晒太阳', tags=['cat'], scope='private')\n"
        "r\n"
    )
    code_recall = (
        "import mem\n"
        "results = await mem.recall_memory('猫', limit=5)\n"
        "f'found {len(results)} memories: {results[0][\"content\"] if results else \"none\"}'\n"
    )
    llm = ScriptedLLM([
        execute_python(code_save),
        execute_python(code_recall),
        text("memory test done"),
    ])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("save memory", ctx_id=305)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ya.ToolStep)]
    assert len(tool_steps) >= 2
    assert "猫最喜欢晒太阳" in tool_steps[1].output_text
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── execute_python with schedule service via real daemon ─────────

@pytest.mark.asyncio
async def test_schedule_create_via_execute_python_and_real_daemon(
    db, yuubot_config, test_daemon,
) -> None:
    """Agent creates a schedule through execute_python → real daemon."""
    code = (
        "import schedule\n"
        "r = await schedule.create_schedule('take a break', '0 */2 * * *')\n"
        "r\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("schedule created"),
    ])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("create schedule", ctx_id=306)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ya.ToolStep)]
    assert len(tool_steps) >= 1
    # The daemon is running; schedule.create should succeed
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── Conversation closing via /yclose ─────────────────────────────

@pytest.mark.asyncio
async def test_yclose_clears_session(db, yuubot_config) -> None:
    from yuubot.commands.builtin import build_command_tree
    from yuubot.commands.entry import EntryManager
    from yuubot.daemon.conversation import ConversationManager
    from yuubot.daemon.dispatcher import Dispatcher
    from yuubot.daemon.llm import LLMExecutor

    conv_mgr = ConversationManager(ttl=300, max_tokens=60000)
    runner = AgentRunner(yuubot_config)
    llm_exec = LLMExecutor(conv_mgr=conv_mgr, agent_runner=runner, config=yuubot_config)
    root = build_command_tree(yuubot_config.bot.entries, llm_executor=llm_exec)
    deps = {
        "entry_mgr": EntryManager(),
        "root": root,
        "session_mgr": conv_mgr,
        "config": yuubot_config,
        "agent_runner": runner,
    }
    dispatcher = Dispatcher(
        config=yuubot_config,
        root=root,
        deps=deps,
        agent_runner=runner,
        conv_mgr=conv_mgr,
    )

    llm = ScriptedLLM([text("turn 1"), text("turn 2"), text("turn 3")])

    try:
        with RecorderMock() as recorder:
            with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
                for i in range(3):
                    await dispatcher.dispatch(make_private_event(f"/yllm turn {i}", ctx_id=307))
                    worker_key = "private:10001"
                    worker = dispatcher._workers.get(worker_key)
                    if worker:
                        await asyncio.wait_for(worker.queue.join(), timeout=10)

                # Expire the conversation
                conv = conv_mgr.get(307)
                assert conv is not None
                conv.last_active_at -= conv_mgr.ttl + 1
                conv.created_at = conv.last_active_at - 61

                await dispatcher.dispatch(make_private_event("/yclose", ctx_id=307))
                worker = dispatcher._workers.get(worker_key)
                if worker:
                    await asyncio.wait_for(worker.queue.join(), timeout=10)

        assert conv_mgr.get(307) is None
        assert "没有活跃" in recorder.texts[-1] or "会话" in recorder.texts[-1]
    finally:
        await dispatcher.stop()
        await runner.stop()
