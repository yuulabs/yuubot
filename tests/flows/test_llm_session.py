"""Flow: /yllm triggers agent, session continue, /yclose."""

import asyncio
import json

import pytest

from tests.conftest import (
    MASTER_QQ, FOLK_QQ, GROUP_ID,
    make_group_event, send,
)
from tests.mocks import (
    mock_recorder_api, mock_llm,
    make_text_response, make_tool_call_response,
    OPENAI_RESPONSE_TEXT,
)


async def _wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    """Wait until the per-ctx worker finishes processing its queue."""
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)


async def test_llm_basic_text_response(dispatcher, session_mgr):
    """Master sends /yllm hello → agent runs to completion with history."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm hello", user_id=MASTER_QQ)
        await dispatcher.dispatch(event)
        await _wait_worker(dispatcher, "group:1000")

    session = session_mgr.get(1)  # ctx_id=1
    assert session is not None
    assert session.agent_name == "main"
    # The agent must have actually run — session.history should contain
    # at least the system prompt + user message + assistant response.
    # If agent_runner.run() crashes (e.g. AttributeError), history stays empty.
    assert len(session.history) >= 3, (
        f"Agent did not run to completion: history has {len(session.history)} entries, "
        f"expected >= 3 (system + user + assistant)"
    )


async def test_llm_with_tool_call(dispatcher, session_mgr):
    """Agent makes a tool call (execute_skill_cli → ybot im send) then text."""
    im_send_args = json.dumps({
        "argv": ["ybot", "im", "send", '[{"type":"text","text":"Bot reply!"}]', "--ctx", "1"],
    })

    responses = [
        make_tool_call_response("execute_skill_cli", im_send_args, "call_001"),
        make_text_response("Done!"),
    ]

    with mock_recorder_api() as sent, mock_llm(responses):
        event = make_group_event("/yllm 你好", user_id=MASTER_QQ)
        await dispatcher.dispatch(event)
        await _wait_worker(dispatcher, "group:1000")

    session = session_mgr.get(1)
    assert session is not None
    # Must have history: system + user + tool_call + tool_result + assistant
    assert len(session.history) >= 3, (
        f"Agent did not run to completion: history has {len(session.history)} entries"
    )


async def test_close_session(dispatcher, session_mgr):
    """Master sends /yllm, then /yclose → session is cleared."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm hello", user_id=MASTER_QQ)
        await dispatcher.dispatch(event)
        await _wait_worker(dispatcher, "group:1000")

    assert session_mgr.get(1) is not None

    with mock_recorder_api() as sent:
        event = make_group_event("/yclose", user_id=MASTER_QQ)
        await dispatcher.dispatch(event)
        await _wait_worker(dispatcher, "group:1000")

    assert session_mgr.get(1) is None
    assert len(sent) >= 1
    reply_text = sent[0]["message"][0]["data"]["text"]
    assert "重置" in reply_text or "会话" in reply_text


async def test_general_agent_requires_master(dispatcher):
    """Folk cannot use /yllm #general — needs master role."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm #general do something", user_id=FOLK_QQ)
        await dispatcher.dispatch(event)
        await _wait_worker(dispatcher, "group:1000")

    # Should get permission denied reply
    assert any("权限" in s["message"][0]["data"]["text"] for s in sent)
