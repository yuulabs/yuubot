"""Flow: /yllm triggers agent, session continue, /yclose."""

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


async def test_llm_basic_text_response(dispatcher, session_mgr):
    """Master sends /yllm hello → agent responds with mocked text."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm hello", user_id=MASTER_QQ)
        await send(dispatcher, event, wait=1.0)

    # Agent should have been invoked. Since the mock returns plain text
    # without tool calls, the agent loop finishes immediately.
    # The text response from the LLM is "Hello from mock LLM"
    # but it doesn't call im send — it just completes.
    # A session should still be created.
    session = session_mgr.get(1)  # ctx_id=1
    assert session is not None
    assert session.agent_name == "main"


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
        await send(dispatcher, event, wait=2.0)

    # The tool call to execute_skill_cli would try to run ybot im send
    # in a subprocess. Since we're in test, the subprocess may fail,
    # but the agent loop should still complete.
    session = session_mgr.get(1)
    assert session is not None


async def test_close_session(dispatcher, session_mgr):
    """Master sends /yllm, then /yclose → session is cleared."""
    with mock_recorder_api() as sent, mock_llm():
        # Start a session
        event = make_group_event("/yllm hello", user_id=MASTER_QQ)
        await send(dispatcher, event, wait=1.0)

    assert session_mgr.get(1) is not None

    # Wait for the CtxWorker to finish processing the /yllm event
    worker = dispatcher._workers.get("group:1000")
    if worker:
        await worker.queue.join()

    with mock_recorder_api() as sent:
        # Close the session
        event = make_group_event("/yclose", user_id=MASTER_QQ)
        await send(dispatcher, event, wait=1.0)

    assert session_mgr.get(1) is None
    assert len(sent) >= 1
    reply_text = sent[0]["message"][0]["data"]["text"]
    assert "重置" in reply_text or "会话" in reply_text


async def test_general_agent_requires_master(dispatcher):
    """Folk cannot use /yllm #general — needs master role."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm #general do something", user_id=FOLK_QQ)
        await send(dispatcher, event, wait=0.5)

    # Should get permission denied reply
    assert any("权限" in s["message"][0]["data"]["text"] for s in sent)
