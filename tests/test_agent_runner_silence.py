import asyncio
from types import SimpleNamespace

import pytest

from yuubot.daemon.agent_runner import AgentRunner


class _FakeAgent:
    def __init__(self) -> None:
        self.flow = SimpleNamespace(stem=[])
        self.sent: list[tuple[str, bool]] = []

    def send(self, content: str, *, defer_tools: bool = False) -> None:
        self.sent.append((content, defer_tools))


class _FakeSession:
    def __init__(self, *, silence_timeout: float, stem: list | None = None) -> None:
        self.config = SimpleNamespace(silence_timeout=silence_timeout)
        self._agent = _FakeAgent()
        if stem is not None:
            self._agent.flow.stem = stem


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
    content, defer_tools = session._agent.sent[0]
    assert defer_tools is True
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
