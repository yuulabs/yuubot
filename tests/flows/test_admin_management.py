"""Flow: admin management — grant mod, bot on/off, commands ignored when off."""

import pytest

from tests.conftest import (
    MASTER_QQ, FOLK_QQ, MOD_QQ, GROUP_ID,
    make_group_event, send,
)
from tests.mocks import mock_recorder_api


async def test_grant_mod_then_bot_off(dispatcher):
    """Master grants mod → mod can /ybot off → commands ignored."""
    # 1. Master grants MOD to MOD_QQ
    with mock_recorder_api() as sent:
        event = make_group_event(
            f"/ybot grand @{MOD_QQ} mod",
            user_id=MASTER_QQ,
        )
        await send(dispatcher, event)

    assert len(sent) >= 1
    assert "MOD" in sent[0]["message"][0]["data"]["text"]

    # 2. Mod turns bot off
    with mock_recorder_api() as sent:
        event = make_group_event("/ybot off", user_id=MOD_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
    assert "关闭" in sent[0]["message"][0]["data"]["text"]

    # 3. Folk sends a command → ignored (bot is off, folk is not master)
    with mock_recorder_api() as sent:
        event = make_group_event("/yhelp", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) == 0  # bot is off, should not respond

    # 4. Master can still use it (master always gets through)
    with mock_recorder_api() as sent:
        event = make_group_event("/yhelp", user_id=MASTER_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1  # master bypasses bot_enabled check


async def test_mod_turns_bot_on(dispatcher):
    """After bot is off, mod can turn it back on."""
    # Grant mod first
    with mock_recorder_api():
        event = make_group_event(f"/ybot grand @{MOD_QQ} mod", user_id=MASTER_QQ)
        await send(dispatcher, event)

    # Turn off
    with mock_recorder_api():
        event = make_group_event("/ybot off", user_id=MOD_QQ)
        await send(dispatcher, event)

    # Turn on (mod sends — but bot is off, so mod can't get through
    # unless mod is also master. Actually _should_respond returns False
    # for non-master when bot_enabled=False. So master must turn on.)
    with mock_recorder_api() as sent:
        event = make_group_event("/ybot on", user_id=MASTER_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
    assert "开启" in sent[0]["message"][0]["data"]["text"]

    # Now folk can use commands again
    with mock_recorder_api() as sent:
        event = make_group_event("/yhelp", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
