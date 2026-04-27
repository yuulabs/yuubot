"""E2E tests for LLM basic interaction — routing, system prompt, tool spec, plain text reply."""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
import yuullm

from yuubot.daemon.agent_runner import AgentRunner
from yuubot.core.onebot import to_inbound_message
from tests.conftest import (
    make_group_event,
    make_private_event,
)
from tests.framework import ScriptedLLM, RecorderMock, text, tool_call


# ── Routing: master bare text → maid ─────────────────────────────

@pytest.mark.asyncio
async def test_master_plain_text_routes_to_maid(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("你好！")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock() as recorder:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_conversation(
                to_inbound_message(make_private_event("hello", ctx_id=100)),
                agent_name="maid",
                bot_kind="master",
            )

    assert session is not None
    assert session.final_text == "你好！"
    assert recorder.texts == ["你好！"]
    assert llm.calls, "LLM was never called"
    await runner.stop()


# ── Routing: group @bot → yuu ────────────────────────────────────

@pytest.mark.asyncio
async def test_group_atbot_routes_to_yuu(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("收到！")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock() as recorder:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_conversation(
                to_inbound_message(make_group_event("hello", ctx_id=101)),
                agent_name="yuu",
                bot_kind="group",
            )

    assert session is not None
    assert session.final_text == "收到！"
    assert recorder.texts == ["收到！"]
    assert llm.calls
    await runner.stop()


# ── System prompt contains expected sections ─────────────────────

@pytest.mark.asyncio
async def test_yuu_system_prompt_contains_core_sections(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_conversation(
                to_inbound_message(make_group_event("hello", ctx_id=102)),
                agent_name="yuu",
                bot_kind="group",
            )

    sp = llm.system_prompt
    assert "SESSION_STATE" in sp or "execute_python" in sp
    assert "受限" in sp or "restricted" in sp.lower() or "worker" in sp.lower()
    await runner.stop()


@pytest.mark.asyncio
async def test_maid_system_prompt_contains_kernel_backend_info(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_conversation(
                to_inbound_message(make_private_event("hello", ctx_id=103)),
                agent_name="maid",
                bot_kind="master",
            )

    sp = llm.system_prompt
    assert "SESSION_STATE" in sp or "execute_python" in sp
    assert "kernel" in sp.lower() or "master" in sp.lower()
    await runner.stop()


# ── Tool spec includes execute_python ─────────────────────────────

@pytest.mark.asyncio
async def test_yuu_tool_spec_includes_execute_python(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_conversation(
                to_inbound_message(make_group_event("hello", ctx_id=104)),
                agent_name="yuu",
                bot_kind="group",
            )

    assert "execute_python" in llm.tool_names
    await runner.stop()


@pytest.mark.asyncio
async def test_maid_tool_spec_includes_file_tools(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_conversation(
                to_inbound_message(make_private_event("hello", ctx_id=105)),
                agent_name="maid",
                bot_kind="master",
            )

    names = llm.tool_names
    assert "execute_python" in names
    assert "read_file" in names or "edit_file" in names
    await runner.stop()


# ── Expand functions appear in system prompt ──────────────────────

@pytest.mark.asyncio
async def test_yuu_expand_functions_in_system_prompt(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_conversation(
                to_inbound_message(make_group_event("hello", ctx_id=106)),
                agent_name="yuu",
                bot_kind="group",
            )

    sp = llm.system_prompt
    assert "im.send_message" in sp
    assert "im.recent_messages" in sp
    assert "im.send_message" not in llm.tool_descriptions_text
    await runner.stop()


# ── User message XML format ──────────────────────────────────────

@pytest.mark.asyncio
async def test_user_message_renders_as_xml_with_timestamp(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = AgentRunner(yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_conversation(
                to_inbound_message(make_private_event("你好世界", ctx_id=107)),
                agent_name="maid",
                bot_kind="master",
            )

    user_text = "\n".join(llm.user_texts)
    assert "<msg" in user_text, f"Expected XML <msg> in user text, got: {user_text[:200]}"
    assert "你好世界" in user_text
    assert re.search(r"现在是 \d{4}年\d{2}月\d{2}日 \d{2}时\d{2}分\d{2}秒", user_text), (
        f"Expected time prefix, got: {user_text[:200]}"
    )
    await runner.stop()


# ── Multiple LLM calls in multi-turn conversation ────────────────

@pytest.mark.asyncio
async def test_conversation_continuation_second_llm_call(db, yuubot_config) -> None:
    llm = ScriptedLLM([
        text("first reply"),
        text("second reply"),
    ])
    runner = AgentRunner(yuubot_config)
    inbound = to_inbound_message(make_private_event("hello", ctx_id=108))

    with RecorderMock() as recorder:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            s1 = await runner.run_conversation(inbound, agent_name="maid", bot_kind="master")
            s2 = await runner.run_conversation(inbound, agent_name="maid", bot_kind="master")

    assert s1 is not None and s2 is not None
    assert s1.final_text == "first reply"
    assert s2.final_text == "second reply"
    assert len(llm.calls) == 2
    assert recorder.texts == ["first reply", "second reply"]
    await runner.stop()
