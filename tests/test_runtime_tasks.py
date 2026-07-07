from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yuubot.app import Yuubot
from yuubot.runtime.tasks import (
    TaskNotRunningError,
    register_shell_task,
    schedule_task_delivery,
    write_task_stdin,
)


@pytest.mark.asyncio
async def test_shell_task_runs_in_pty_and_streams_stdout(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        name="echo",
        shell="echo hello-pty",
        intro="pty test",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    await record.wait_terminal()
    assert record.status == "done"
    assert record.exit_code == 0
    assert "hello-pty" in record.stdout.tail(max_bytes=1024)
    assert record.interactive is True
    assert record.created_at
    assert record.started_at
    assert record.finished_at


@pytest.mark.asyncio
async def test_shell_task_accepts_stdin(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        name="read",
        shell="python3 -c 'import sys; line=sys.stdin.readline(); print(f\"got:{line.strip()}\")'",
        intro="stdin test",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    for _ in range(100):
        if record.is_terminal():
            break
        await asyncio.sleep(0.02)
    assert not record.is_terminal()
    write_task_stdin(record, "answer\n")
    await record.wait_terminal()
    assert record.status == "done"
    assert "got:answer" in record.stdout.tail(max_bytes=1024)


@pytest.mark.asyncio
async def test_write_task_stdin_rejects_terminal_task(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        name="done",
        shell="true",
        intro="done",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    await record.wait_terminal()
    with pytest.raises(TaskNotRunningError):
        write_task_stdin(record, "x")


@pytest.mark.asyncio
async def test_task_delivery_queues_while_conversation_busy(tmp_path: Path) -> None:
    from yuubot.actor import ActorConfig
    from yuubot.domain import ModelCard
    from yuubot.llm import scripted_reply

    app = await Yuubot.create(tmp_path / "data")
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    conversation = await app.runtime.conversations.get_or_create(actor, "busy-c1")
    conversation._running = True
    record = register_shell_task(
        app.runtime,
        name="bg",
        shell="true",
        intro="delivery queue",
        owner="actor:amy:conv:busy-c1",
        workspace=Path(actor.config.workspace),
    )
    await record.wait_terminal()
    await schedule_task_delivery(app.runtime, record)
    assert record.delivery_state == "pending"
    assert app.runtime.task_delivery_queue._pending.get("busy-c1") == [record.id]
    conversation._running = False
    await app.runtime.drain_pending_task_deliveries("busy-c1")
    assert record.delivery_state == "delivered"
