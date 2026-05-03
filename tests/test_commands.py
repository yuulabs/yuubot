"""E2E tests for deterministic command system — user-facing QQ responses."""

from __future__ import annotations

import asyncio

import pytest

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager
from yuubot.daemon.dispatcher import Dispatcher
from tests.conftest import (
    FOLK_QQ,
    make_group_event,
    make_private_event,
)
from tests.framework import RecorderMock
from tests.helpers import sent_texts


def _make_dispatcher(yuubot_config):
    root = build_command_tree(yuubot_config.bot.entries)
    deps = {
        "entry_mgr": EntryManager(),
        "root": root,
        "config": yuubot_config,
    }
    dispatcher = Dispatcher(
        config=yuubot_config,
        root=root,
        deps=deps,
    )
    return dispatcher, None


# ── /yhelp ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_help_returns_general_help_text(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_private_event("/yhelp"))
        await asyncio.sleep(0.1)

    assert recorder.texts
    assert any("命令" in t for t in recorder.texts)
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_help_for_specific_command(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_private_event("/yhelp ping"))
        await asyncio.sleep(0.1)

    assert recorder.texts
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_help_unknown_command_returns_error(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_private_event("/yhelp nonexistentcmd"))
        await asyncio.sleep(0.1)

    assert recorder.texts
    assert any("未知" in t for t in recorder.texts)
    await dispatcher.stop()


# ── /yping ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ping_returns_pong_without_active_session(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_private_event("/yping"))
        await asyncio.sleep(0.1)

    assert recorder.texts == ["pong"]
    await dispatcher.stop()


# ── /yclose ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_without_active_session(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_private_event("/yclose"))
        await asyncio.sleep(0.1)

    assert recorder.texts
    assert any("没有活跃" in t or "会话" in t for t in recorder.texts)
    await dispatcher.stop()


# ── /ybot on / /ybot off ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bot_on_enables_bot_in_group(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_group_event("/ybot on", user_id=FOLK_QQ, at_bot=False))
        await asyncio.sleep(0.1)

    assert recorder.texts == ["Bot 已开启"]
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_bot_off_disables_bot_in_group(db, yuubot_config) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_group_event("/ybot off", user_id=FOLK_QQ, at_bot=False))
        await asyncio.sleep(0.1)

    assert recorder.texts
    assert any("关闭" in t or "制动" in t for t in recorder.texts)
    await dispatcher.stop()


# ── /yhhsh ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yhhsh_translates_abbreviation(db, yuubot_config) -> None:
    from tests.mocks import mock_hhsh_api

    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder, mock_hhsh_api():
        await dispatcher.dispatch(make_group_event("/yhhsh yyds", user_id=FOLK_QQ, at_bot=False))
        await asyncio.sleep(0.1)

    assert len(recorder.sent) == 1
    assert "永远的神" in sent_texts(recorder.sent)[0]
    await dispatcher.stop()


# ── /ycost ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_reports_no_records_when_empty(db, yuubot_config, traces_db) -> None:
    dispatcher, _ = _make_dispatcher(yuubot_config)

    with RecorderMock() as recorder:
        await dispatcher.dispatch(make_private_event("/ycost"))
        await asyncio.sleep(0.1)

    assert recorder.texts
    assert any("没有" in t or "定价" in t for t in recorder.texts)
    await dispatcher.stop()
