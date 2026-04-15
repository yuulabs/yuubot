from __future__ import annotations

from typing import cast

import yuullm

from yuubot.daemon.agent_runner import AgentRunner


def test_summarize_history_delta_includes_tool_and_text() -> None:
    before = cast(list[yuullm.Message], [
        ("user", [{"type": "text", "text": "@夕雨yuu 一点半喊一声中午好"}]),
    ])
    after = cast(list[yuullm.Message], [
        *before,
        ("assistant", [{"type": "tool_call", "name": "call_cap_cli", "arguments": "{}"}]),
        ("tool", [{"type": "text", "text": "已创建定时任务 [id: 40]"}]),
        ("assistant", [{"type": "text", "text": "好的，已经创建好了。"}]),
    ])

    summary = AgentRunner._summarize_history_delta(before, after)

    assert "[assistant tool_calls=call_cap_cli]" in summary
    assert '[tool text="已创建定时任务 [id: 40]"]' in summary
    assert '[assistant text="好的，已经创建好了。"]' in summary


def test_summarize_history_delta_returns_none_when_unchanged() -> None:
    history = cast(list[yuullm.Message], [
        ("assistant", [{"type": "text", "text": "pong"}]),
    ])

    assert AgentRunner._summarize_history_delta(history, history) == "none"
