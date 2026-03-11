"""Flow: private chat — user-visible behavior only."""

from tests.conftest import MASTER_QQ, FOLK_QQ, make_group_event, make_private_event, send
from tests.helpers import history_text, sent_texts
from tests.mocks import (
    make_text_response,
    mock_llm,
    mock_recorder_api,
)


async def test_master_dm_always_works(dispatcher, session_mgr):
    """Master 私聊 /yllm 一定会被受理并建立 session。"""
    with mock_recorder_api(), mock_llm([make_text_response("master-dm-ok")]):
        await send(
            dispatcher,
            make_private_event("/yllm hello", user_id=MASTER_QQ),
            wait=1.0,
        )

    session = session_mgr.get(2)
    assert session is not None
    assert session.agent_name == "main"
    assert "master-dm-ok" in history_text(session.history)


async def test_non_whitelisted_dm_ignored(dispatcher):
    """非白名单用户私聊默认不应得到响应。"""
    with mock_recorder_api() as sent:
        await send(dispatcher, make_private_event("/yhelp", user_id=FOLK_QQ))

    assert sent == []


async def test_allow_dm_then_help_works(dispatcher):
    """Master 显式 allow-dm 后，对方私聊普通命令可以得到回复。"""
    with mock_recorder_api() as sent:
        await send(
            dispatcher,
            make_group_event(f"/ybot allow-dm @{FOLK_QQ}", user_id=MASTER_QQ),
        )
    assert any("允许" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await send(dispatcher, make_private_event("/yhelp", user_id=FOLK_QQ))

    assert any("help" in text or "子命令" in text for text in sent_texts(sent))
