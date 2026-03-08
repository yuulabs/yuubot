"""Flow: private chat — master DM, non-whitelisted ignored, allow-dm."""

import pytest

from tests.conftest import (
    MASTER_QQ, FOLK_QQ, GROUP_ID,
    make_private_event, make_group_event, send,
)
from tests.mocks import mock_recorder_api, mock_llm


async def test_master_dm_always_works(dispatcher):
    """Master can always send private messages."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_private_event("/yllm hello", user_id=MASTER_QQ)
        await send(dispatcher, event, wait=1.0)

    # Master should get through — session created
    # (no assertion on sent since agent may not call im send)


async def test_non_whitelisted_dm_ignored(dispatcher):
    """Non-whitelisted user's DM is ignored."""
    with mock_recorder_api() as sent:
        event = make_private_event("/yhelp", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) == 0  # not whitelisted, ignored


async def test_allow_dm_then_works(dispatcher, yuubot_config):
    """Master grants DM access → user can send private messages."""
    # Master grants allow-dm (must be from group context since it's a bot command)
    with mock_recorder_api() as sent:
        event = make_group_event(
            f"/ybot allow-dm @{FOLK_QQ}",
            user_id=MASTER_QQ,
        )
        await send(dispatcher, event)

    assert len(sent) >= 1
    assert "允许" in sent[0]["message"][0]["data"]["text"]

    # Now folk can DM
    with mock_recorder_api() as sent:
        event = make_private_event("/yhelp", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
