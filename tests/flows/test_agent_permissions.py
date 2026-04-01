"""Flow: agent permission checks — requirement-level behavior."""

from tests.conftest import MASTER_QQ, FOLK_QQ, make_group_event, send
from tests.helpers import history_text, sent_texts
from tests.mocks import mock_llm, mock_recorder_api, make_text_response
# ── Runtime: folk user rejected from master agent ────────────────


async def test_folk_rejected_from_master_agent(dispatcher):
    """Folk user using #general (master-only) gets permission denied."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm #general hello", user_id=FOLK_QQ)
        await send(dispatcher, event, wait=0.5)

    assert any("权限" in text for text in sent_texts(sent))


async def test_master_can_use_master_agent(dispatcher, session_mgr):
    """Master 可以选中 master-only agent，并进入对应 session。"""
    reply = "general-agent-ready"
    with mock_recorder_api(), mock_llm([make_text_response(reply)]):
        event = make_group_event("/yllm #general hello", user_id=MASTER_QQ)
        await send(dispatcher, event, wait=1.0)

    session = session_mgr.get(1)
    assert session is not None
    assert session.agent_name == "general"
    assert reply in history_text(session.history)
