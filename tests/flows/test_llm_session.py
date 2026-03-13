"""Flow: /yllm, session continuation, and explicit close."""

import asyncio

from tests.conftest import MASTER_QQ, FOLK_QQ, make_group_event
from tests.helpers import history_text, sent_texts
from tests.mocks import (
    make_text_response,
    make_tool_call_response,
    mock_llm,
    mock_recorder_api,
)


async def _wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    """Wait until the per-ctx worker finishes processing its queue."""
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)


async def test_llm_creates_session_with_assistant_reply(dispatcher, session_mgr):
    """`/yllm` 应创建 session，并把 assistant 回复写入会话历史。"""
    reply = "assistant says hello"
    with mock_recorder_api(), mock_llm([make_text_response(reply)]):
        await dispatcher.dispatch(make_group_event("/yllm hello", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000")

    session = session_mgr.get(1)
    assert session is not None
    assert session.agent_name == "main"
    assert reply in history_text(session.history)


async def test_llm_tool_call_creates_session(dispatcher, session_mgr):
    """LLM 返回 tool_call 时，agent 应正常执行完毕并建立 session。"""
    responses = [
        make_tool_call_response(
            "execute_skill_cli",
            '{"command": "echo ok"}',
            "call_001",
        ),
        make_text_response("Done!"),
    ]

    with mock_recorder_api(), mock_llm(responses):
        await dispatcher.dispatch(make_group_event("/yllm 你好", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000")

    session = session_mgr.get(1)
    assert session is not None
    assert "Done!" in history_text(session.history)


async def test_group_session_requires_at_for_continuation(dispatcher, session_mgr):
    """群聊里已有 session 时，不 @bot 的普通消息不应被并入会话。"""
    first = "first turn"
    second = "second turn"
    with mock_recorder_api(), mock_llm(
        [make_text_response(first), make_text_response(second)]
    ):
        await dispatcher.dispatch(make_group_event("/yllm hello", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000")

        session = session_mgr.get(1)
        assert session is not None
        baseline = history_text(session.history)

        await dispatcher.dispatch(
            make_group_event("this should be ignored", user_id=MASTER_QQ, at_bot=False)
        )
        await _wait_worker(dispatcher, "group:1000")

        session = session_mgr.get(1)
        assert session is not None
        assert history_text(session.history) == baseline

        await dispatcher.dispatch(
            make_group_event("please continue", user_id=MASTER_QQ, at_bot=True)
        )
        await _wait_worker(dispatcher, "group:1000")

    session = session_mgr.get(1)
    assert session is not None
    assert second in history_text(session.history)


async def test_close_session(dispatcher, session_mgr):
    """`/yclose` 应关闭当前活跃 session，并给出显式反馈。"""
    with mock_recorder_api(), mock_llm([make_text_response("hello")]):
        await dispatcher.dispatch(make_group_event("/yllm hello", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000")

    assert session_mgr.get(1) is not None

    with mock_recorder_api() as sent:
        await dispatcher.dispatch(make_group_event("/yclose", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000")

    assert session_mgr.get(1) is None
    assert any("重置" in text or "会话" in text for text in sent_texts(sent))


async def test_general_agent_requires_master(dispatcher):
    """master-only agent 对 Folk 必须返回权限不足。"""
    with mock_recorder_api() as sent, mock_llm():
        await dispatcher.dispatch(
            make_group_event("/yllm #general do something", user_id=FOLK_QQ)
        )
        await _wait_worker(dispatcher, "group:1000")

    assert any("权限" in text for text in sent_texts(sent))


async def test_rollover_stashes_summary_after_final_response(dispatcher, session_mgr, monkeypatch):
    """Rollover should pause only after this turn already produced a final response."""
    session_mgr.max_tokens = 1
    monkeypatch.setattr(
        dispatcher.agent_runner,
        "summarize",
        lambda history, agent_name="main": asyncio.sleep(0, result="已完成数据库 WAL 的背景解释"),
    )

    with mock_recorder_api() as sent, mock_llm(
        [make_text_response("第一轮回复"), make_text_response("第二轮回复")]
    ):
        await dispatcher.dispatch(make_group_event("/yllm 解释 WAL", user_id=MASTER_QQ))
        await _wait_worker(dispatcher, "group:1000")

        session = session_mgr.get(1)
        assert session is not None
        assert session.history == []
        assert "压缩摘要" in session.summary_prompt
        assert not any("继续处理中" in text for text in sent_texts(sent))
        session_mgr.max_tokens = 60000

        await dispatcher.dispatch(
            make_group_event("再补充一下 checkpoint 的关系", user_id=MASTER_QQ, at_bot=True)
        )
        await _wait_worker(dispatcher, "group:1000")

    session = session_mgr.get(1)
    assert session is not None
    assert session.summary_prompt == ""
    assert "第二轮回复" in history_text(session.history)
