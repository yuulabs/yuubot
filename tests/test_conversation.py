"""Tests for Conversation and ConversationManager."""

from yuubot.commands.builtin import _exec_ping
from yuubot.commands.tree import CommandRequest
from yuubot.core.onebot import to_inbound_message
from yuubot.daemon.conversation import (
    Conversation,
    ConversationManager,
    conversation_worth_curating,
)


def _make_inbound(text: str):
    return to_inbound_message(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 1,
            "user_id": 100,
            "message": [{"type": "text", "data": {"text": text}}],
            "raw_message": text,
            "time": 1700000000,
            "self_id": 99999,
            "sender": {"nickname": "tester", "card": ""},
            "ctx_id": 1,
        }
    )


def test_create_set_running_set_idle():
    """Full lifecycle: create → set_running → set_idle."""
    mgr = ConversationManager(ttl=300, max_tokens=60000)

    conv = mgr.create(ctx_id=1, agent_name="main", user_id=100)
    assert conv.state == "idle"
    assert conv.started_by == 100

    mgr.set_running(1)
    conv = mgr.get(1)
    assert conv.state == "running"

    mgr.set_idle(1)
    assert conv.state == "idle"


def test_conversation_worth_curating():
    """conversation_worth_curating checks turns and duration."""
    conv = Conversation(ctx_id=1, agent_name="main")
    conv.history = [("assistant", ["hi"])] * 2
    assert not conversation_worth_curating(conv)

    conv.history = [("assistant", ["hi"])] * 3
    conv.created_at = conv.last_active_at - 120
    assert conversation_worth_curating(conv)


def test_close_returns_conversations():
    """close() returns all closed conversations."""
    mgr = ConversationManager()
    mgr.create(ctx_id=1, agent_name="main")
    closed = mgr.close(1)
    assert len(closed) == 1
    assert mgr.get(1) is None


def test_update_session_token_limit():
    """update_session returns True and removes conv when token limit hit."""
    mgr = ConversationManager(max_tokens=100)
    conv = mgr.create(ctx_id=1, agent_name="main")
    session = type("Session", (), {"history": [("user", ["hi"])], "total_tokens": 200})()

    rolled = mgr.update_session(conv, session)
    assert rolled is True
    assert mgr.get(1) is None


def test_running_conversation_does_not_expire():
    """A running conversation should not be expired even if TTL is exceeded."""
    mgr = ConversationManager(ttl=0.0)  # immediate expiry
    mgr.create(ctx_id=1, agent_name="main")
    mgr.set_running(1)

    # Should not expire because state is running
    result = mgr.get(1)
    assert result is not None
    assert result.state == "running"


def test_create_resets_summary_prompt():
    """New conversations start clean unless rollover explicitly injects a handoff."""
    mgr = ConversationManager()
    conv = mgr.create(ctx_id=1, agent_name="main")
    conv.summary_prompt = "stale"

    conv2 = mgr.create(ctx_id=1, agent_name="main")
    assert conv2.summary_prompt == ""


async def test_ping_without_conversation_returns_plain_pong():
    mgr = ConversationManager()

    result = await _exec_ping(
        CommandRequest(
            remaining="",
            message=_make_inbound("ping"),
            deps={"session_mgr": mgr},
            command_path=("ping",),
            entry="/y",
        )
    )

    assert result == "pong"


async def test_ping_running_conversation_returns_session_pong():
    mgr = ConversationManager()
    mgr.create(ctx_id=1, agent_name="main")
    mgr.set_running(1)

    result = await _exec_ping(
        CommandRequest(
            remaining="",
            message=_make_inbound("ping"),
            deps={"session_mgr": mgr},
            command_path=("ping",),
            entry="/y",
        )
    )

    assert result == "session pong"


async def test_ping_idle_conversation_returns_session_ready():
    mgr = ConversationManager()
    mgr.create(ctx_id=1, agent_name="main")

    result = await _exec_ping(
        CommandRequest(
            remaining="",
            message=_make_inbound("ping"),
            deps={"session_mgr": mgr},
            command_path=("ping",),
            entry="/y",
        )
    )

    assert result == "session ready"
