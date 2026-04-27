from __future__ import annotations

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.tree import CommandRequest
from yuubot.commands.ychar import exec_char_config
from yuubot.core.onebot import to_inbound_message
from tests.conftest import make_private_event


def test_ychar_route_is_registered(yuubot_config) -> None:
    root = build_command_tree(yuubot_config.bot.entries)

    matched = root.match_message("/ychar alias test/test-model as deepseek-chat")

    assert matched is not None
    assert matched.command_path == ("char", "alias")
    assert matched.remaining == "test/test-model as deepseek-chat"


async def test_ychar_config_updates_runtime_agent_ref(yuubot_config) -> None:
    inbound = to_inbound_message(make_private_event("/ychar config general llm=test/test-model-v2"))
    request = CommandRequest(
        remaining="general llm=test/test-model-v2",
        message=inbound,
        deps={"config": yuubot_config},
        command_path=("char", "config"),
        entry="/y",
    )

    response = await exec_char_config(request)

    assert response is not None
    assert "已更新 general" in response
    assert yuubot_config.agent_llm_refs["general"] == "test/test-model-v2"
