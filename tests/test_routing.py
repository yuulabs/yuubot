"""Tests for daemon/routing.py — pure routing logic."""

from __future__ import annotations

from yuubot.commands.builtin import build_command_tree
from yuubot.core.models import AtSegment, TextSegment
from yuubot.core.types import (
    CommandRoute,
    ConversationRoute,
    InboundMessage,
    Sender,
)
from yuubot.daemon.routing import resolve_route


BOT_QQ = 12345


async def _noop_llm(remaining, event, deps):
    return None


def _make_tree():
    return build_command_tree(["/y", "/yuu"], llm_executor=_noop_llm)


def _make_msg(segments, ctx_id=1, chat_type="private"):
    return InboundMessage(
        message_id=1,
        ctx_id=ctx_id,
        chat_type=chat_type,
        sender=Sender(user_id=99),
        segments=segments,
        timestamp=0,
        raw_event={},
    )


async def test_at_bot_yields_conversation_route():
    root = _make_tree()
    msg = _make_msg([AtSegment(qq=str(BOT_QQ)), TextSegment(text="hello")])
    route = resolve_route(msg, root, lambda _: False, lambda _: False, BOT_QQ)
    assert isinstance(route, ConversationRoute)
    assert route.text == "continue hello"
    assert route.is_continuation is False


async def test_at_bot_with_active_session():
    root = _make_tree()
    msg = _make_msg([AtSegment(qq=str(BOT_QQ)), TextSegment(text="hello")])
    route = resolve_route(msg, root, lambda _: True, lambda _: False, BOT_QQ)
    assert isinstance(route, ConversationRoute)
    assert route.is_continuation is True


async def test_yllm_command_route():
    root = _make_tree()
    msg = _make_msg([TextSegment(text="/yllm hello")])
    route = resolve_route(msg, root, lambda _: False, lambda _: False, BOT_QQ)
    assert isinstance(route, CommandRoute)
    assert route.command == "llm"
    assert route.remaining == "hello"


async def test_ybot_on_command_route():
    root = _make_tree()
    msg = _make_msg([TextSegment(text="/ybot on")])
    route = resolve_route(msg, root, lambda _: False, lambda _: False, BOT_QQ)
    assert isinstance(route, CommandRoute)
    assert route.command == "on"


async def test_auto_mode_bare_text():
    root = _make_tree()
    msg = _make_msg([TextSegment(text="just chatting")], chat_type="private")
    route = resolve_route(msg, root, lambda _: False, lambda _: True, BOT_QQ)
    assert isinstance(route, ConversationRoute)
    assert route.text == "continue just chatting"


async def test_auto_mode_group_ignored():
    root = _make_tree()
    msg = _make_msg([TextSegment(text="just chatting")], chat_type="group")
    route = resolve_route(msg, root, lambda _: False, lambda _: True, BOT_QQ)
    assert route is None


async def test_unmatched_text_returns_none():
    root = _make_tree()
    msg = _make_msg([TextSegment(text="random stuff")])
    route = resolve_route(msg, root, lambda _: False, lambda _: False, BOT_QQ)
    assert route is None
