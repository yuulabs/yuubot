from __future__ import annotations

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager
from yuubot.commands.hhsh import exec_hhsh
from yuubot.commands.tree import CommandRequest
from yuubot.core.onebot import to_inbound_message
from yuubot.daemon.conversation import ConversationManager
from yuubot.daemon.dispatcher import Dispatcher
from tests.conftest import FOLK_QQ, make_group_event
from tests.mocks import mock_hhsh_api, mock_recorder_api


def test_yhhsh_route_is_registered(yuubot_config) -> None:
    root = build_command_tree(yuubot_config.bot.entries)

    matched = root.match_message("/yhhsh scp")

    assert matched is not None
    assert matched.command_path == ("hhsh",)
    assert matched.remaining == "scp"


async def test_hhsh_executor_translates_abbreviation() -> None:
    inbound = to_inbound_message(make_group_event("/yhhsh yyds"))
    request = CommandRequest(
        remaining="yyds",
        message=inbound,
        deps={},
        command_path=("hhsh",),
        entry="/y",
    )

    with mock_hhsh_api():
        response = await exec_hhsh(request)

    assert response is not None
    assert "永远的神" in response


async def test_yhhsh_group_command_dispatches_without_at(db, yuubot_config) -> None:
    root = build_command_tree(yuubot_config.bot.entries)
    conv_mgr = ConversationManager(ttl=300, max_tokens=60000)
    deps = {
        "entry_mgr": EntryManager(),
        "root": root,
        "session_mgr": conv_mgr,
        "config": yuubot_config,
    }
    dispatcher = Dispatcher(
        config=yuubot_config,
        root=root,
        deps=deps,
        agent_runner=object(),
        conv_mgr=conv_mgr,
    )

    with mock_recorder_api() as sent, mock_hhsh_api():
        await dispatcher.dispatch(make_group_event("/yhhsh yyds", user_id=FOLK_QQ, at_bot=False))

    assert len(sent) == 1
    assert "永远的神" in sent[0]["message"][0]["data"]["text"]
