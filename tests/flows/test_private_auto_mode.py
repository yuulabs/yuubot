"""Flow: private auto mode should be observable through message behavior."""

from __future__ import annotations

from tests.conftest import MASTER_QQ, make_private_event
from tests.helpers import history_text, sent_texts, wait_worker
from tests.mocks import make_text_response, mock_llm, mock_recorder_api


async def test_private_auto_mode_accepts_bare_text_and_can_be_disabled(
    dispatcher,
    session_mgr,
) -> None:
    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_private_event("/ybot on --auto", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    assert any("开启 auto" in text for text in sent_texts(sent))

    with mock_recorder_api(), mock_llm([make_text_response("auto-mode-reply")]):
        await dispatcher.dispatch(make_private_event("直接继续聊", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    session = session_mgr.get(2)
    assert session is not None
    assert session.agent_name == "main"
    assert "auto-mode-reply" in history_text(session.history)

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_private_event("/ybot off", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    assert any("关闭 auto" in text or "紧急制动" in text for text in sent_texts(sent))

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_private_event("这次不该再自动回复", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    assert sent == []


async def test_explicit_yllm_command_in_auto_mode_switches_back_to_main(
    dispatcher,
    session_mgr,
) -> None:
    with mock_recorder_api():
        await dispatcher.dispatch(make_private_event("/ybot on --auto", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    with mock_recorder_api(), mock_llm([make_text_response("general-ready")]):
        await dispatcher.dispatch(make_private_event("/yllm #general hello", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    session = session_mgr.get(2)
    assert session is not None
    assert session.agent_name == "general"
    assert "general-ready" in history_text(session.history)

    with mock_recorder_api(), mock_llm([make_text_response("main-ready")]):
        await dispatcher.dispatch(make_private_event("/yllm#main hello", user_id=MASTER_QQ))
        await wait_worker(dispatcher, f"private:{MASTER_QQ}")

    session = session_mgr.get(2)
    assert session is not None
    assert session.agent_name == "main"
    assert "main-ready" in history_text(session.history)
