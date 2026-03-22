import asyncio

import pytest

from yuubot.commands.builtin import _exec_ping
from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import CommandRequest
from yuubot.core.onebot import to_inbound_message
from yuubot.daemon.conversation import ConversationManager
from yuubot.daemon.llm import LLMExecutor
from yuubot.daemon.llm import _has_final_response


def test_has_final_response_detects_text_reply():
    history = [
        ("user", [{"type": "text", "text": "问题"}]),
        ("assistant", [{"type": "tool_call", "name": "web.read"}]),
        ("assistant", [{"type": "text", "text": "最终回复"}]),
    ]

    assert _has_final_response(history) is True


def test_has_final_response_ignores_tool_only_turn():
    history = [
        ("user", [{"type": "text", "text": "问题"}]),
        ("assistant", [{"type": "tool_call", "name": "web.read"}]),
    ]

    assert _has_final_response(history) is False


@pytest.mark.asyncio
async def test_ping_reports_running_while_llm_turn_is_in_flight(yuubot_config, monkeypatch):
    class StubAgentRunner:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run_conversation(self, message, **kwargs):
            del message, kwargs
            self.started.set()
            await self.release.wait()
            return type(
                "Session",
                (),
                {
                    "task_id": "task-1",
                    "history": [("assistant", [{"type": "text", "text": "done"}])],
                    "total_tokens": 1,
                },
            )()

    conv_mgr = ConversationManager(ttl=300, max_tokens=60000)
    role_mgr = RoleManager(master_qq=yuubot_config.bot.master)
    agent_runner = StubAgentRunner()
    executor = LLMExecutor(
        conv_mgr=conv_mgr,
        agent_runner=agent_runner,
        config=yuubot_config,
        role_mgr=role_mgr,
    )
    monkeypatch.setattr("yuubot.daemon.llm._send_reply", _noop_send_reply)

    llm_message = to_inbound_message(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 1,
            "user_id": yuubot_config.bot.master,
            "message": [{"type": "text", "data": {"text": "/yllm hi"}}],
            "raw_message": "/yllm hi",
            "time": 1700000000,
            "self_id": 99999,
            "sender": {"nickname": "tester", "card": ""},
            "ctx_id": 1,
        }
    )
    ping_message = to_inbound_message(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 2,
            "user_id": yuubot_config.bot.master,
            "message": [{"type": "text", "data": {"text": "/yping"}}],
            "raw_message": "/yping",
            "time": 1700000001,
            "self_id": 99999,
            "sender": {"nickname": "tester", "card": ""},
            "ctx_id": 1,
        }
    )

    llm_task = asyncio.create_task(
        executor(
            CommandRequest(
                remaining="hi",
                message=llm_message,
                deps={"session_mgr": conv_mgr},
                command_path=("llm",),
                entry="/y",
            )
        )
    )
    await agent_runner.started.wait()

    during = await _exec_ping(
        CommandRequest(
            remaining="",
            message=ping_message,
            deps={"session_mgr": conv_mgr},
            command_path=("ping",),
            entry="/y",
        )
    )

    agent_runner.release.set()
    await llm_task

    after = await _exec_ping(
        CommandRequest(
            remaining="",
            message=ping_message,
            deps={"session_mgr": conv_mgr},
            command_path=("ping",),
            entry="/y",
        )
    )

    assert during == "session pong"
    assert after == "session ready"


async def _noop_send_reply(message, text, config) -> None:
    del message, text, config


@pytest.mark.asyncio
async def test_explicit_yllm_in_auto_mode_respects_main(yuubot_config, monkeypatch):
    class StubAgentRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def run_conversation(self, message, **kwargs):
            del message
            self.calls.append(kwargs)
            return type(
                "Session",
                (),
                {
                    "task_id": "task-main",
                    "history": [("assistant", [{"type": "text", "text": "done"}])],
                    "total_tokens": 1,
                },
            )()

    conv_mgr = ConversationManager(ttl=300, max_tokens=60000)
    conv_mgr._auto_ctxs.add(1)
    conv_mgr._current_agent[1] = "general"
    role_mgr = RoleManager(master_qq=yuubot_config.bot.master)
    agent_runner = StubAgentRunner()
    executor = LLMExecutor(
        conv_mgr=conv_mgr,
        agent_runner=agent_runner,
        config=yuubot_config,
        role_mgr=role_mgr,
    )
    monkeypatch.setattr("yuubot.daemon.llm._send_reply", _noop_send_reply)

    message = to_inbound_message(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 3,
            "user_id": yuubot_config.bot.master,
            "message": [{"type": "text", "data": {"text": "/yllm hello"}}],
            "raw_message": "/yllm hello",
            "time": 1700000002,
            "self_id": 99999,
            "sender": {"nickname": "tester", "card": ""},
            "ctx_id": 1,
        }
    )

    await executor(
        CommandRequest(
            remaining="hello",
            message=message,
            deps={"session_mgr": conv_mgr},
            command_path=("llm",),
            entry="/y",
        )
    )

    assert agent_runner.calls[0]["agent_name"] == "main"
    session = conv_mgr.get(1)
    assert session is not None
    assert session.agent_name == "main"


@pytest.mark.asyncio
async def test_explicit_yllm_main_in_auto_mode_respects_main(yuubot_config, monkeypatch):
    class StubAgentRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def run_conversation(self, message, **kwargs):
            del message
            self.calls.append(kwargs)
            return type(
                "Session",
                (),
                {
                    "task_id": "task-main",
                    "history": [("assistant", [{"type": "text", "text": "done"}])],
                    "total_tokens": 1,
                },
            )()

    conv_mgr = ConversationManager(ttl=300, max_tokens=60000)
    conv_mgr._auto_ctxs.add(1)
    conv_mgr._current_agent[1] = "general"
    role_mgr = RoleManager(master_qq=yuubot_config.bot.master)
    agent_runner = StubAgentRunner()
    executor = LLMExecutor(
        conv_mgr=conv_mgr,
        agent_runner=agent_runner,
        config=yuubot_config,
        role_mgr=role_mgr,
    )
    monkeypatch.setattr("yuubot.daemon.llm._send_reply", _noop_send_reply)

    message = to_inbound_message(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 4,
            "user_id": yuubot_config.bot.master,
            "message": [{"type": "text", "data": {"text": "/yllm#main hello"}}],
            "raw_message": "/yllm#main hello",
            "time": 1700000003,
            "self_id": 99999,
            "sender": {"nickname": "tester", "card": ""},
            "ctx_id": 1,
        }
    )

    await executor(
        CommandRequest(
            remaining="#main hello",
            message=message,
            deps={"session_mgr": conv_mgr},
            command_path=("llm",),
            entry="/y",
        )
    )

    assert agent_runner.calls[0]["agent_name"] == "main"
    session = conv_mgr.get(1)
    assert session is not None
    assert session.agent_name == "main"
