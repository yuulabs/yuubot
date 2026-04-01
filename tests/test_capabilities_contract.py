from __future__ import annotations

import pytest

from yuubot.capabilities import CapabilityContext, execute, load_capability_doc
from yuubot.capabilities.contract import ActionFilter
from yuubot.capabilities.tools import call_cap_cli


def test_load_capability_doc_respects_action_filter():
    doc = load_capability_doc(
        "mem",
        action_filter=ActionFilter(mode="include", actions=frozenset({"save", "recall"})),
    )

    assert "### save" in doc
    assert "### recall" in doc
    assert "### delete" not in doc
    assert "### restore" not in doc


async def test_execute_rejects_disallowed_action():
    with pytest.raises(ValueError, match="not available to this agent"):
        await execute(
            "mem delete 1",
            context=CapabilityContext(
                agent_name="main",
                action_filters={
                    "mem": ActionFilter(mode="include", actions=frozenset({"save", "recall"}))
                },
            ),
        )


async def test_call_cap_cli_preserves_media_path_context(monkeypatch):
    captured: dict[str, CapabilityContext] = {}

    async def fake_execute(command: str, *, context: CapabilityContext | None = None):
        captured["context"] = context
        return [{"type": "text", "text": "ok"}]

    monkeypatch.setattr("yuubot.capabilities.tools.execute", fake_execute, raising=False)
    monkeypatch.setattr("yuubot.capabilities.execute", fake_execute)

    addon_context = CapabilityContext(
        agent_name="main",
        docker_host_mount="/mnt/host",
        docker_home_host_dir="/home/tester",
        docker_home_dir="/root",
    )
    result = await call_cap_cli.fn("im send --ctx 1 -- []", addon_context=addon_context)

    assert result == "ok"
    assert captured["context"].docker_host_mount == "/mnt/host"
    assert captured["context"].docker_home_host_dir == "/home/tester"
    assert captured["context"].docker_home_dir == "/root"
