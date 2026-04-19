from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import yuullm

from yuubot.daemon.agent_runner import AgentRunner


def test_count_im_send_calls_counts_only_matching_assistant_tool_calls() -> None:
    session = SimpleNamespace(history=cast(list[yuullm.Message], [
        ("assistant", [{"type": "tool_call", "name": "call_cap_cli", "arguments": "im send --ctx 1 -- []"}]),
        ("assistant", [{"type": "tool_call", "name": "call_cap_cli", "arguments": "mem list"}]),
        ("assistant", [{"type": "tool_call", "name": "other", "arguments": "im send --ctx 1 -- []"}]),
        ("user", [{"type": "tool_call", "name": "call_cap_cli", "arguments": "im send --ctx 1 -- []"}]),
        ("assistant", [{"type": "text", "text": "still working"}]),
        ("assistant", [{"type": "tool_call", "name": "call_cap_cli", "arguments": "im send --ctx 1 -- []"}]),
    ]))

    assert AgentRunner._count_im_send_calls(session) == 2


def test_count_im_send_calls_ignores_non_string_arguments() -> None:
    session = SimpleNamespace(history=cast(list[yuullm.Message], [
        ("assistant", [{"type": "tool_call", "name": "call_cap_cli", "arguments": {"cmd": "im send"}}]),
        ("assistant", [{"type": "tool_call", "name": "call_cap_cli", "arguments": None}]),
    ]))

    assert AgentRunner._count_im_send_calls(session) == 0
