"""End-to-end tests for daemon.render — RenderPolicy, render_task, replace_command_prefix."""

from __future__ import annotations

import pytest

from yuubot.core.models import AtSegment, TextSegment
from yuubot.core.types import InboundMessage, Sender
from yuubot.daemon.render import (
    RenderContext,
    RenderPolicy,
    render_ping_payload,
    render_task,
    replace_command_prefix,
)

from tests.conftest import BOT_QQ


# ── Helpers ───────────────────────────────────────────────────────


def _make_msg(
    text: str,
    *,
    at_bot: bool = False,
    user_id: int = 20001,
    ctx_id: int = 1,
    chat_type: str = "group",
    nickname: str = "Alice",
    group_id: int = 1000,
) -> InboundMessage:
    """Build an InboundMessage for testing."""
    segments = []
    if at_bot:
        segments.append(AtSegment(qq=str(BOT_QQ)))
    segments.append(TextSegment(text=text))

    event = {
        "post_type": "message",
        "message_type": chat_type,
        "message_id": 42,
        "user_id": user_id,
        "group_id": group_id,
        "message": [],
        "raw_message": text,
        "time": 1700000000,
        "self_id": BOT_QQ,
        "sender": {"nickname": nickname, "card": ""},
        "ctx_id": ctx_id,
    }

    return InboundMessage(
        message_id=42,
        ctx_id=ctx_id,
        chat_type=chat_type,
        sender=Sender(user_id=user_id, nickname=nickname),
        segments=segments,
        timestamp=1700000000,
        raw_event=event,
    )


# ── replace_command_prefix ───────────────────────────────────────


def test_replace_command_prefix_yllm():
    segs = [TextSegment(text="/yllm hello world")]
    result = replace_command_prefix(segs, "夕雨")
    assert len(result) == 1
    assert isinstance(result[0], TextSegment)
    assert result[0].text == "@夕雨 hello world"


def test_replace_command_prefix_with_agent_suffix():
    segs = [TextSegment(text="/y#general do something")]
    result = replace_command_prefix(segs, "Bot")
    assert result[0].text == "@Bot do something"


def test_replace_command_prefix_no_match():
    segs = [TextSegment(text="hello world")]
    result = replace_command_prefix(segs, "Bot")
    assert result is segs  # same list, unmodified


def test_replace_command_prefix_skips_non_text():
    """ReplySegment before TextSegment should be skipped."""
    from yuubot.core.models import ReplySegment

    segs = [ReplySegment(id="123"), TextSegment(text="/yuu greet")]
    result = replace_command_prefix(segs, "Bot")
    assert isinstance(result[1], TextSegment)
    assert result[1].text == "@Bot greet"


# ── strip_bot_at ─────────────────────────────────────────────────


async def test_strip_bot_at(db):
    """With strip_bot_at=True, @bot segments are removed from output."""
    msg = _make_msg(" hello", at_bot=True)
    policy = RenderPolicy(strip_bot_at=True)
    ctx = RenderContext(bot_qq=str(BOT_QQ), bot_name="Bot")

    result = await render_task(msg, policy, ctx)
    # The @bot should not appear as @99999 in rendered XML
    assert f"@{BOT_QQ}" not in result
    assert "hello" in result


async def test_no_strip_bot_at(db):
    """With strip_bot_at=False, @bot segments are preserved."""
    msg = _make_msg(" hello", at_bot=True)
    policy = RenderPolicy(strip_bot_at=False)
    ctx = RenderContext(bot_qq=str(BOT_QQ), bot_name="Bot")

    result = await render_task(msg, policy, ctx)
    assert f"@{BOT_QQ}" in result


# ── render_task snapshot ─────────────────────────────────────────


async def test_render_task_xml_snapshot(db):
    """Snapshot: given fixed input, output contains expected XML structure."""
    msg = _make_msg("你好世界", nickname="Alice", user_id=20001, ctx_id=1)
    policy = RenderPolicy()
    ctx = RenderContext(group_name="测试群", bot_name="Bot", bot_qq=str(BOT_QQ))

    result = await render_task(msg, policy, ctx)
    assert '<msg id="42"' in result
    assert 'name="Alice"' in result
    assert 'qq="20001"' in result
    assert "你好世界" in result
    assert "测试群" in result
    assert "ctx 1" in result


async def test_render_task_continuation(db):
    """Continuation mode omits location preamble."""
    msg = _make_msg("继续聊")
    policy = RenderPolicy()
    ctx = RenderContext(bot_qq=str(BOT_QQ))

    result = await render_task(msg, policy, ctx, is_continuation=True)
    assert "你收到了来自" not in result
    assert "继续聊" in result


# ── render_ping_payload ──────────────────────────────────────────


async def test_render_ping_payload(db):
    """Ping payload renders a single <msg> tag."""
    msg = _make_msg("ping消息")
    policy = RenderPolicy()
    ctx = RenderContext(bot_qq=str(BOT_QQ), bot_name="Bot")

    result = await render_ping_payload(msg, policy, ctx)
    assert "<msg" in result
    assert "ping消息" in result
