"""E2E tests for LLM tool-calling — execute_python, multi-tool sequences, real daemon for agent-fns."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yuullm

from yuubot.core.onebot import to_inbound_message
from tests.framework import ActorTestRunner, ToolStep
from tests.conftest import make_group_event, make_private_event
from tests.framework import ScriptedLLM, RecorderMock, text, execute_python


# ── execute_python: simple computation ───────────────────────────

@pytest.mark.asyncio
async def test_execute_python_simple_computation(db, yuubot_config) -> None:
    llm = ScriptedLLM([
        execute_python("2 + 3"),
        text("结果是5"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock() as recorder:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("算一下", ctx_id=200)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "5" in tool_steps[0].output_text
    assert session.final_text == "结果是5"
    await runner.stop()


# ── execute_python: access SESSION_STATE and TASKS ───────────────

@pytest.mark.asyncio
async def test_execute_python_accesses_session_state(db, yuubot_config) -> None:
    code = "f'ctx={SESSION_STATE.ctx_id} bot={SESSION_STATE.bot_kind}'"
    llm = ScriptedLLM([
        execute_python(code),
        text("done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=201)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "ctx=201" in tool_steps[0].output_text
    assert "master" in tool_steps[0].output_text
    await runner.stop()


@pytest.mark.asyncio
async def test_execute_python_tasks_dict_is_writable(db, yuubot_config) -> None:
    code = "TASKS['test_key'] = 'hello'; 'ok'"
    llm = ScriptedLLM([
        execute_python(code),
        text("done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("set task", ctx_id=202)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "Python execution failed" not in tool_steps[0].output_text
    assert "ok" in tool_steps[0].output_text
    await runner.stop()


# ── execute_python: import modules ───────────────────────────────

@pytest.mark.asyncio
async def test_execute_python_imports_json_module(db, yuubot_config) -> None:
    code = "import json; json.dumps({'a': 1})"
    llm = ScriptedLLM([
        execute_python(code),
        text("done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=203)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "a" in tool_steps[0].output_text and "1" in tool_steps[0].output_text
    await runner.stop()


# ── restricted python sandbox ────────────────────────────────────

@pytest.mark.asyncio
async def test_group_restricted_python_blocks_file_access(db, yuubot_config) -> None:
    code = "import os; os.listdir('.')"
    llm = ScriptedLLM([
        execute_python(code),
        text("reply"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_group_event("test", ctx_id=204)),
                agent_name="yuu",
                bot_kind="group",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "SyntaxError" in tool_steps[0].output_text or "disabled" in tool_steps[0].output_text.lower() or "restricted" in tool_steps[0].output_text.lower()
    await runner.stop()


@pytest.mark.asyncio
async def test_group_restricted_python_blocks_while_loop(db, yuubot_config) -> None:
    code = "i = 0\nwhile i < 3:\n    i += 1"
    llm = ScriptedLLM([
        execute_python(code),
        text("reply"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_group_event("test", ctx_id=205)),
                agent_name="yuu",
                bot_kind="group",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "while" in tool_steps[0].output_text.lower() or "disabled" in tool_steps[0].output_text.lower()
    await runner.stop()


# ── Tool error handling ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_python_syntax_error_is_captured(db, yuubot_config) -> None:
    code = "this is not valid python @@@"
    llm = ScriptedLLM([
        execute_python(code),
        text("got error"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("test", ctx_id=206)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    # Error should be captured; LLM should have received it and produced text
    assert session.final_text == "got error"
    await runner.stop()


# ── Multi-tool sequence ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_then_tool_call_then_text(db, yuubot_config) -> None:
    llm = ScriptedLLM([
        execute_python("x = [1, 2, 3]; sum(x)"),
        execute_python("x.append(4); sum(x)"),
        text("all done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock() as _recorder:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("compute", ctx_id=207)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 2
    assert "6" in tool_steps[0].output_text
    assert "10" in tool_steps[1].output_text
    assert session.final_text == "all done"
    await runner.stop()


# ── Master kernel session reuse ──────────────────────────────────

@pytest.mark.asyncio
async def test_master_kernel_session_persists_variables_across_turns(db, yuubot_config) -> None:
    llm = ScriptedLLM([
        execute_python("shared_var = 42\nshared_var"),
        text("set"),
        execute_python("shared_var + 1"),
        text("got it"),
    ])
    runner = ActorTestRunner(config=yuubot_config)
    inbound = to_inbound_message(make_private_event("hello", ctx_id=208))

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            s1 = await runner.run_direct_turn(inbound, agent_name="shiori", bot_kind="master")
            s2 = await runner.run_direct_turn(inbound, agent_name="shiori", bot_kind="master")

    assert s1 is not None and s2 is not None
    tool1 = [s for s in s1.steps if isinstance(s, ToolStep)][0]
    tool2 = [s for s in s2.steps if isinstance(s, ToolStep)][0]
    assert "42" in tool1.output_text
    assert "43" in tool2.output_text
    await runner.stop()


@pytest.mark.asyncio
async def test_group_restricted_worker_does_not_persist_variables(db, yuubot_config) -> None:
    llm = ScriptedLLM([
        execute_python("gvar = 99\ngvar"),
        text("set"),
        execute_python("gvar"),
        text("reply"),
    ])
    runner = ActorTestRunner(config=yuubot_config)
    inbound = to_inbound_message(make_group_event("hello", ctx_id=209))

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            s1 = await runner.run_direct_turn(inbound, agent_name="yuu", bot_kind="group")
            s2 = await runner.run_direct_turn(inbound, agent_name="yuu", bot_kind="group")

    assert s1 is not None and s2 is not None
    tool2 = [s for s in s2.steps if isinstance(s, ToolStep)][0]
    # Restricted worker starts fresh each turn; gvar should not persist
    assert "NameError" in tool2.output_text or "not defined" in tool2.output_text
    await runner.stop()
