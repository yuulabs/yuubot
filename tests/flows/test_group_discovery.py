"""Flow: basic group commands — /yhelp, /yhhsh, permission checks."""


from tests.conftest import (
    MASTER_QQ, FOLK_QQ, make_group_event, send,
)
from tests.mocks import mock_recorder_api, mock_hhsh_api


async def test_help_returns_command_list(dispatcher):
    """Folk user sends /yhelp → gets help text with command list."""
    with mock_recorder_api() as sent:
        event = make_group_event("/yhelp", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
    reply_text = sent[0]["message"][0]["data"]["text"]
    # Help should mention at least some commands
    assert "help" in reply_text or "子命令" in reply_text


async def test_hhsh_translates_abbreviation(dispatcher):
    """Folk user sends /yhhsh yyds → gets translated result."""
    with mock_recorder_api() as sent, mock_hhsh_api():
        event = make_group_event("/yhhsh yyds", user_id=FOLK_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
    reply_text = sent[0]["message"][0]["data"]["text"]
    assert "永远的神" in reply_text


async def test_folk_cannot_use_bot_cmd(dispatcher):
    """Folk user cannot run /ybot on — needs MOD."""
    with mock_recorder_api() as sent:
        event = make_group_event("/ybot on", user_id=FOLK_QQ)
        await send(dispatcher, event)

    # Should be silently ignored (permission denied = no reply)
    assert len(sent) == 0


async def test_master_bot_on_free(dispatcher):
    """Master sends /ybot on --free → bot enabled in free mode."""
    with mock_recorder_api() as sent:
        event = make_group_event("/ybot on --free", user_id=MASTER_QQ)
        await send(dispatcher, event)

    assert len(sent) >= 1
    reply_text = sent[0]["message"][0]["data"]["text"]
    assert "free" in reply_text or "开启" in reply_text
