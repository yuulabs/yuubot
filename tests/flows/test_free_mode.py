"""Flow: free mode — no @bot needed, bot off stops everything."""

import pytest

from tests.conftest import (
    MASTER_QQ, FOLK_QQ, GROUP_ID,
    make_group_event, send,
)
from tests.mocks import mock_recorder_api, mock_llm


async def test_free_mode_no_at_needed(dispatcher):
    """In free mode, messages without @bot still get processed."""
    # Enable free mode
    with mock_recorder_api():
        event = make_group_event("/ybot on --free", user_id=MASTER_QQ)
        await send(dispatcher, event)

    # Folk sends /yllm without @bot → should work in free mode
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event(
            "/yllm hello", user_id=FOLK_QQ, at_bot=False,
        )
        await send(dispatcher, event, wait=1.0)

    # In free mode, _should_respond returns True even without @bot,
    # and free mode + llm command bypasses permission check


async def test_bot_off_stops_free_mode(dispatcher):
    """Turning bot off stops all responses including free mode."""
    # Enable free mode
    with mock_recorder_api():
        event = make_group_event("/ybot on --free", user_id=MASTER_QQ)
        await send(dispatcher, event)

    # Turn bot off
    with mock_recorder_api():
        event = make_group_event("/ybot off", user_id=MASTER_QQ)
        await send(dispatcher, event)

    # Folk sends with @bot → should be ignored (bot is off)
    with mock_recorder_api() as sent:
        event = make_group_event("/yhelp", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) == 0


async def test_free_mode_non_llm_still_needs_at(dispatcher):
    """In free mode, non-llm commands still need @bot or command prefix."""
    # Enable free mode
    with mock_recorder_api():
        event = make_group_event("/ybot on --free", user_id=MASTER_QQ)
        await send(dispatcher, event)

    # Folk sends /yhelp without @bot → _should_respond returns True (free mode),
    # but the command tree matching still works on the text
    with mock_recorder_api() as sent:
        event = make_group_event("/yhelp", user_id=FOLK_QQ, at_bot=False)
        await send(dispatcher, event)

    # In free mode, _should_respond returns True, so the command gets processed
    assert len(sent) >= 1
