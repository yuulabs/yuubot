"""Flow: free mode — user-visible behavior only."""

from tests.conftest import MASTER_QQ, FOLK_QQ, make_group_event, send
from tests.helpers import history_text, sent_texts
from tests.mocks import (
    make_text_response,
    mock_llm,
    mock_recorder_api,
)


async def test_free_mode_accepts_llm_without_at_and_creates_session(
    dispatcher, session_mgr,
):
    """群聊 free 模式下，/yllm 不 @bot 也会被受理并建立 session。"""
    with mock_recorder_api(), mock_llm([make_text_response("free-mode-reply")]):
        await send(dispatcher, make_group_event("/ybot on --free", user_id=MASTER_QQ))
        await send(
            dispatcher,
            make_group_event("/yllm hello", user_id=FOLK_QQ, at_bot=False),
            wait=1.0,
        )

    session = session_mgr.get(1)
    assert session is not None
    assert session.agent_name == "main"
    assert "free-mode-reply" in history_text(session.history)


async def test_bot_off_blocks_free_mode_traffic(dispatcher):
    """群聊关闭后，free 模式下的普通用户消息应被忽略。"""
    with mock_recorder_api():
        await send(dispatcher, make_group_event("/ybot on --free", user_id=MASTER_QQ))
    with mock_recorder_api():
        await send(dispatcher, make_group_event("/ybot off", user_id=MASTER_QQ))

    with mock_recorder_api() as sent:
        await send(
            dispatcher,
            make_group_event("/yllm hello", user_id=FOLK_QQ, at_bot=False),
            wait=0.5,
        )

    assert sent == []


async def test_free_mode_still_allows_normal_prefixed_commands(dispatcher):
    """free 模式下，普通前缀命令无需 @bot 也能执行。"""
    with mock_recorder_api():
        await send(dispatcher, make_group_event("/ybot on --free", user_id=MASTER_QQ))

    with mock_recorder_api() as sent:
        await send(
            dispatcher,
            make_group_event("/yhelp", user_id=FOLK_QQ, at_bot=False),
        )

    assert any("help" in text or "子命令" in text for text in sent_texts(sent))
