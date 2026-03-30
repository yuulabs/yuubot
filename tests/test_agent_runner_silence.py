import asyncio
from types import SimpleNamespace

import pytest
import yuullm

from yuubot.daemon.agent_runner import AgentRunner


class _FakeAgent:
    def __init__(self) -> None:
        self.flow = SimpleNamespace(stem=[])
        self.sent: list[tuple[yuullm.Message, bool]] = []

    def send(self, message: yuullm.Message, *, defer_tools: bool = False) -> None:
        self.sent.append((message, defer_tools))


class _FakeSession:
    def __init__(self, *, silence_timeout: float, stem: list | None = None) -> None:
        self.config = SimpleNamespace(silence_timeout=silence_timeout)
        self._agent = _FakeAgent()
        if stem is not None:
            self._agent.flow.stem = stem

    @property
    def agent(self):
        return self._agent

    def has_tool_call(self, tool_name: str, *, argument_contains: str = "") -> bool:
        for event in self._agent.flow.stem:
            if not isinstance(event, yuullm.ToolCall):
                name = getattr(event, "name", None)
                if name != tool_name:
                    continue
                arguments = getattr(event, "arguments", "") or ""
                if argument_contains and argument_contains not in arguments:
                    continue
                return True
            if event.name != tool_name:
                continue
            if argument_contains and argument_contains not in (event.arguments or ""):
                continue
            return True
        return False

    def send(self, message: yuullm.Message, *, defer_tools: bool = False) -> None:
        self._agent.send(message, defer_tools=defer_tools)


@pytest.mark.asyncio
async def test_silence_timeout_defers_and_injects_progress_prompt(yuubot_config):
    runner = AgentRunner(yuubot_config)
    session = _FakeSession(silence_timeout=0.01)
    runner._active_runs_by_runtime["runtime-1"] = SimpleNamespace(runtime_id="runtime-1")

    await runner._watch_silence_timeout(
        runtime_id="runtime-1",
        agent_name="main",
        session=session,
        ctx_id=1,
    )

    assert len(session._agent.sent) == 1
    message, defer_tools = session._agent.sent[0]
    assert defer_tools is True
    assert message[0] == "user"
    content = "".join(item["text"] for item in message[1] if item.get("type") == "text")
    assert "im send" in content


@pytest.mark.asyncio
async def test_silence_timeout_skips_when_im_send_already_used(yuubot_config):
    runner = AgentRunner(yuubot_config)
    stem = [SimpleNamespace(name="call_cap_cli", arguments='{"command":"im send --ctx 1 -- []"}')]
    session = _FakeSession(silence_timeout=0.01, stem=stem)
    runner._active_runs_by_runtime["runtime-1"] = SimpleNamespace(runtime_id="runtime-1")

    await runner._watch_silence_timeout(
        runtime_id="runtime-1",
        agent_name="main",
        session=session,
        ctx_id=1,
    )

    assert session._agent.sent == []
