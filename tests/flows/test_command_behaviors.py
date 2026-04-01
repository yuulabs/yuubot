"""Flow: user-visible command behavior for session and character commands."""

from __future__ import annotations

from yuubot.characters import register
from yuubot.prompt import AgentSpec, Character

from tests.conftest import FOLK_QQ, MASTER_QQ, make_group_event
from tests.helpers import sent_texts, wait_worker
from tests.mocks import make_text_response, mock_llm, mock_recorder_api


async def test_ping_reflects_session_lifecycle(dispatcher) -> None:
    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/yping", user_id=MASTER_QQ))
        await wait_worker(dispatcher, "group:1000")

    assert any(text == "pong" for text in sent_texts(sent))

    with mock_recorder_api(), mock_llm([make_text_response("session-ready")]):
        await dispatcher.dispatch(make_group_event("/yllm hello", user_id=MASTER_QQ))
        await wait_worker(dispatcher, "group:1000")

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/yping", user_id=MASTER_QQ))
        await wait_worker(dispatcher, "group:1000")

    assert any("session ready" in text for text in sent_texts(sent))

    with mock_recorder_api():
        await dispatcher.dispatch(make_group_event("/yclose", user_id=MASTER_QQ))
        await wait_worker(dispatcher, "group:1000")

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/yping", user_id=MASTER_QQ))
        await wait_worker(dispatcher, "group:1000")

    assert any(text == "pong" for text in sent_texts(sent))


async def test_bot_set_creates_working_entry_alias(dispatcher) -> None:
    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ybot set /foo help", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("入口 /foo → help" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/foo", user_id=MASTER_QQ, at_bot=False)
        )
        await wait_worker(dispatcher, "group:1000")

    alias_text = "\n".join(sent_texts(sent))
    assert "help" in alias_text or "子命令" in alias_text


async def test_folk_cannot_use_char_management(dispatcher) -> None:
    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/ychar list", user_id=FOLK_QQ))
        await wait_worker(dispatcher, "group:1000")

    assert sent == []


async def test_char_commands_list_show_and_mutate_runtime_config(dispatcher) -> None:
    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/ychar list", user_id=MASTER_QQ))
        await wait_worker(dispatcher, "group:1000")

    list_text = "\n".join(sent_texts(sent))
    assert "main" in list_text
    assert "general" in list_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar show config main", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    config_text = "\n".join(sent_texts(sent))
    assert "Character: main" in config_text
    assert "LLM ref: test/test-model" in config_text
    assert "Resolved: test/test-model" in config_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event(
                "/ychar config main llm=or/sonnet",
                user_id=MASTER_QQ,
            )
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("openrouter" in text for text in sent_texts(sent))
    assert any("claude-sonnet-4.1" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar show config main", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    mutated_text = "\n".join(sent_texts(sent))
    assert "LLM ref: or/sonnet" in mutated_text
    assert "Resolved: openrouter/anthropic/claude-sonnet-4.1" in mutated_text
    assert "Family: claude" in mutated_text
    assert "Vision: True" in mutated_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar alias * as sonnet", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("已绑定 sonnet" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event(
                "/ychar alias test/test-model as sonnet", user_id=MASTER_QQ
            )
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("已绑定 sonnet: test/test-model" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar alias show sonnet", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    alias_text = "\n".join(sent_texts(sent))
    assert "Selector: sonnet" in alias_text
    assert "Manual bindings:" in alias_text
    assert "openrouter=anthropic/claude-sonnet-4.1" in alias_text
    assert "test=test-model" in alias_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar alias delete or/sonnet", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("已删除 or/sonnet" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar alias show sonnet", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    alias_text = "\n".join(sent_texts(sent))
    assert "test=test-model" in alias_text
    assert "openrouter=anthropic/claude-sonnet-4.1" not in alias_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar role list", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    role_list_text = "\n".join(sent_texts(sent))
    assert "vision" in role_list_text
    assert "selector" in role_list_text
    assert "summarizer" in role_list_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar role show vision", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    role_text = "\n".join(sent_texts(sent))
    assert "Role: vision" in role_text
    assert "Selector: gemini-3.1-flash-lite-preview" in role_text
    assert "Resolved: aihubmix/google/gemini-3.1-flash-lite-preview" in role_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar role set vision openrouter", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("override=provider=openrouter" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar role show vision", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    role_text = "\n".join(sent_texts(sent))
    assert "Override: provider=openrouter" in role_text
    assert "Resolved: openrouter/google/gemini-3.1-flash-lite-preview" in role_text

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar role clear vision", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    assert any("已清除 role=vision" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar role show vision", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    role_text = "\n".join(sent_texts(sent))
    assert "Override: (none)" in role_text
    assert "Resolved: aihubmix/google/gemini-3.1-flash-lite-preview" in role_text


async def test_char_show_prompt_reports_control_tools(dispatcher) -> None:
    register(
        Character(
            name="worker",
            description="Background worker",
            min_role="master",
            persona="你是后台 worker。",
            spec=AgentSpec(
                tools=["sleep", "inspect_background", "wait_background", "delegate"],
            ),
            provider="test",
            model="test-model",
        )
    )

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(
            make_group_event("/ychar show prompt worker", user_id=MASTER_QQ)
        )
        await wait_worker(dispatcher, "group:1000")

    prompt_text = "\n".join(sent_texts(sent))
    assert "Character: worker" in prompt_text
    assert "control_tools" in prompt_text
    assert "inspect_background" in prompt_text
    assert "wait_background" in prompt_text
