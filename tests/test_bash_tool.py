from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yuubot.app import Yuubot
from yuubot.chat.harness import Harness, HarnessConfig
from yuubot.domain.messages import ConversationContext, ModelCard
from yuubot.domain.stream import ToolCall
from yuubot.tools.base import ToolConfig


def _noop_emit(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.mark.asyncio
async def test_bash_fast_command_returns_sync_result(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ConversationContext(
        model=ModelCard(selector="test"),
        conversation_id="bash-fast",
        actor="amy",
        workspace=workspace,
    )
    harness = Harness.from_config(
        HarnessConfig(tools={"bash": ToolConfig(type="bash")}),
        context,
        app.runtime,
    )
    results = await harness.gather(
        [ToolCall(id="call-1", name="bash", arguments='{"command":"echo hello-sync"}')],
        asyncio.Event(),
    )
    await harness.close()

    text = results[0].content[0].text
    assert "exit_code: 0" in text
    assert "hello-sync" in text
    assert "detached:" not in text


@pytest.mark.asyncio
async def test_bash_detaches_on_stdout_idle(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ConversationContext(
        model=ModelCard(selector="test"),
        conversation_id="bash-idle",
        actor="amy",
        workspace=workspace,
    )
    harness = Harness.from_config(
        HarnessConfig(tools={"bash": ToolConfig(type="bash")}),
        context,
        app.runtime,
    )
    results = await harness.gather(
        [
            ToolCall(
                id="call-1",
                name="bash",
                arguments='{"command":"sleep 30","idle_timeout_s":0.2}',
            )
        ],
        asyncio.Event(),
        timeout=5.0,
    )
    await harness.close()

    text = results[0].content[0].text
    assert "detached: true" in text
    assert "task_id: t-" in text
    assert "yb.tasks.find" in text
    [record] = app.runtime.tasks.list()
    assert record.ttl_s == 3600


@pytest.mark.asyncio
async def test_bash_detaches_for_stdin_waiting_command(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ConversationContext(
        model=ModelCard(selector="test"),
        conversation_id="bash-stdin",
        actor="amy",
        workspace=workspace,
    )
    harness = Harness.from_config(
        HarnessConfig(tools={"bash": ToolConfig(type="bash")}),
        context,
        app.runtime,
    )
    results = await harness.gather(
        [
            ToolCall(
                id="call-1",
                name="bash",
                arguments=(
                    '{"command":"python3 -c \\"import sys; print(\\\\\\"prompt\\\\\\"); '
                    'sys.stdout.flush(); sys.stdin.readline()\\"","idle_timeout_s":0.3}'
                ),
            )
        ],
        asyncio.Event(),
        timeout=5.0,
    )
    await harness.close()

    text = results[0].content[0].text
    assert "detached: true" in text
    assert "prompt" in text
