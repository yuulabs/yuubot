"""E2E tests for LLM basic interaction — routing, system prompt, tool spec, plain text reply."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
import yuullm
import yuuagents as ya

from yuubot.characters import register, unregister
from tests.framework import ActorTestRunner
from yuubot.core.onebot import to_inbound_message
from yuubot.prompt import AgentSpec, Character, ExpandFunctionsSection, InlineSection
from tests.conftest import (
    make_group_event,
    make_private_event,
)
from tests.framework import ScriptedLLM, RecorderMock, text


def _call_text(call) -> str:
    parts: list[str] = []
    for message in call.messages:
        for item in message.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
    return "\n".join(parts)


# ── Routing: master bare text -> shiori ─────────────────────────────

@pytest.mark.asyncio
async def test_master_plain_text_routes_to_shiori(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("你好！")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=100)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    assert session.final_text == "你好！"
    assert llm.calls, "LLM was never called"
    await runner.stop()


# ── Routing: group @bot → yuu ────────────────────────────────────

@pytest.mark.asyncio
async def test_group_atbot_routes_to_yuu(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("收到！")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_group_event("hello", ctx_id=101)),
                agent_name="yuu",
                bot_kind="group",
            )

    assert session is not None
    assert session.final_text == "收到！"
    assert llm.calls
    await runner.stop()


# ── System prompt contains expected sections ─────────────────────

@pytest.mark.asyncio
async def test_yuu_system_prompt_contains_core_sections(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_group_event("hello", ctx_id=102)),
                agent_name="yuu",
                bot_kind="group",
            )

    sp = llm.system_prompt
    assert "SESSION_STATE" in sp or "execute_python" in sp
    assert "受限" in sp or "restricted" in sp.lower() or "worker" in sp.lower()
    await runner.stop()


@pytest.mark.asyncio
async def test_shiori_system_prompt_contains_kernel_backend_info(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=103)),
                agent_name="shiori",
                bot_kind="master",
            )

    sp = llm.system_prompt
    assert "SESSION_STATE" in sp or "execute_python" in sp
    assert "kernel" in sp.lower() or "master" in sp.lower()
    assert "uv" in sp
    assert "不要默认使用 pip" in sp
    await runner.stop()


# ── Tool spec includes execute_python ─────────────────────────────

@pytest.mark.asyncio
async def test_yuu_tool_spec_includes_execute_python(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_group_event("hello", ctx_id=104)),
                agent_name="yuu",
                bot_kind="group",
            )

    assert "execute_python" in llm.tool_names
    await runner.stop()


@pytest.mark.asyncio
async def test_shiori_tool_spec_includes_file_tools(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=105)),
                agent_name="shiori",
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
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_group_event("hello", ctx_id=106)),
                agent_name="yuu",
                bot_kind="group",
            )

    sp = llm.system_prompt
    assert "im.send_message" in sp
    assert "im.message_records" in sp
    assert "QuerySet[MessageRecord]" in sp
    assert "im.recent_messages" in sp
    assert "prints as formatted XML" in sp
    assert "Each dict has:" in sp
    assert "im.send_message" not in llm.tool_descriptions_text
    await runner.stop()


@pytest.mark.asyncio
async def test_expand_functions_controls_available_packages_in_llm_system_prompt(
    db,
    yuubot_config,
    tmp_path,
) -> None:
    (tmp_path / "probe_tools.py").write_text(
        '"""Probe package."""\n\n'
        "def alpha() -> str:\n"
        '    """Alpha summary.\n\n'
        '    Alpha detail should stay hidden in summary mode.\n'
        '    """\n'
        '    return "alpha"\n\n'
        "def verbose() -> str:\n"
        '    """Verbose summary.\n\n'
        '    Verbose detail should appear only for plus-expanded functions.\n'
        '    """\n'
        '    return "verbose"\n\n'
        "def hidden() -> str:\n"
        '    """Hidden summary should be excluded by the negative glob."""\n'
        '    return "hidden"\n',
        encoding="utf-8",
    )
    character = Character(
        name="expand_probe",
        description="Probe expand_functions rendering.",
        bot_kind="group",
        spec=AgentSpec(
            tools=("execute_python",),
            import_modules=(ya.PythonImport("probe_tools", alias="probe"),),
            expand_functions=("probe.*", "+probe.verbose", "-probe.hidden"),
            prompt_sections=(
                InlineSection("Probe prompt."),
                ExpandFunctionsSection(),
            ),
            max_turns=1,
        ),
    )
    register(character)
    yuubot_config.agent_llm_refs["expand_probe"] = "test/test-model"
    yuubot_config.yuuagents["python"] = {"sys_path": [str(tmp_path)]}
    llm = ScriptedLLM([text("ok")])
    runner = ActorTestRunner(config=yuubot_config)

    try:
        with RecorderMock():
            with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
                await runner.run_direct_turn(
                    to_inbound_message(make_group_event("hello", ctx_id=109)),
                    agent_name="expand_probe",
                    bot_kind="group",
                )
    finally:
        unregister("expand_probe")
        await runner.stop()

    sp = llm.system_prompt
    assert "Available Python packages:" in sp
    assert "- import probe  # alias for probe_tools" in sp
    assert "def probe.alpha() -> str: Alpha summary." in sp
    assert "Alpha detail should stay hidden" not in sp
    assert "def probe.verbose() -> str:" in sp
    assert "Verbose summary." in sp
    assert "Verbose detail should appear only for plus-expanded functions." in sp
    assert "probe.hidden" not in sp
    assert "Hidden summary should be excluded" not in sp
    assert "probe.alpha" not in llm.tool_descriptions_text


@pytest.mark.asyncio
async def test_rollover_summarizes_with_current_agent_history(db, yuubot_config) -> None:
    character = Character(
        name="rollover_probe",
        description="Probe context rollover summarization.",
        bot_kind="group",
        spec=AgentSpec(
            tools=("execute_python",),
            prompt_sections=(InlineSection("Rollover probe prompt."),),
            max_turns=1,
        ),
    )
    register(character)
    yuubot_config.agent_llm_refs["rollover_probe"] = "test/rollover-model"
    llm = ScriptedLLM([
        text("first reply"),
        text("handoff summary"),
        text("second reply"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    try:
        with RecorderMock():
            with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
                await runner.run_direct_turn(
                    to_inbound_message(make_group_event("rollover first", ctx_id=231)),
                    agent_name="rollover_probe",
                    bot_kind="group",
                )
                second = await runner.run_direct_turn(
                    to_inbound_message(make_group_event("rollover second", ctx_id=231)),
                    agent_name="rollover_probe",
                    bot_kind="group",
                )
    finally:
        unregister("rollover_probe")
        await runner.stop()

    assert second.final_text == "second reply"
    assert len(llm.calls) == 3

    summary_call = llm.calls[1]
    summary_text = _call_text(summary_call)
    assert summary_call.model == "rollover-model"
    assert summary_call.tools
    assert summary_call.tools == llm.calls[0].tools
    assert "rollover first" in summary_text
    assert "first reply" in summary_text
    assert "rollover second" in summary_text
    assert "移交" in summary_text

    final_call_text = _call_text(llm.calls[2])
    assert "[上下文摘要]\nhandoff summary" in final_call_text
    assert "rollover second" in final_call_text


# ── User message XML format ──────────────────────────────────────

@pytest.mark.asyncio
async def test_user_message_renders_as_xml_with_timestamp(db, yuubot_config) -> None:
    llm = ScriptedLLM([text("ok")])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_private_event("你好世界", ctx_id=107)),
                agent_name="shiori",
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
    runner = ActorTestRunner(config=yuubot_config)
    inbound = to_inbound_message(make_private_event("hello", ctx_id=108))

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            s1 = await runner.run_direct_turn(inbound, agent_name="shiori", bot_kind="master")
            s2 = await runner.run_direct_turn(inbound, agent_name="shiori", bot_kind="master")

    assert s1 is not None and s2 is not None
    assert s1.final_text == "first reply"
    assert s2.final_text == "second reply"
    assert len(llm.calls) == 2
    await runner.stop()
