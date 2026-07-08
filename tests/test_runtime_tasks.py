from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yuubot.app import Yuubot
from yuubot.runtime.tasks import (
    DEFAULT_TASK_DELIVERY_SUPPRESSION_TTL_S,
    RuntimeTaskRecord,
    TaskDeliveryListener,
    TaskDeliveryQueue,
    TaskNotRunningError,
    register_shell_task,
    schedule_task_delivery,
    wait_until_terminal_or_idle,
    write_task_stdin,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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
    assert record.id not in app.runtime.scheduler._asyncio_tasks
    assert record.id not in app.runtime.tasks._records
    assert record.id in app.runtime.tasks.terminal_records


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
async def test_running_task_is_not_size_evicted(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = Clock()
    app.runtime.tasks.terminal_records.now = clock
    app.runtime.tasks.terminal_records.max_size_bytes = 1
    running = register_shell_task(
        app.runtime,
        name="sleep",
        shell="sleep 30",
        intro="running",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    terminal = RuntimeTaskRecord(
        id="t-terminal",
        owner="actor:amy:conv:c1",
        kind="shell",
        name="terminal",
        status="done",
        delivery="actor",
        delivery_state="delivered",
    )
    terminal.stdout.write("x" * 1024)
    app.runtime.tasks.mark_terminal(terminal)

    clock.advance(61)

    assert running.id in app.runtime.tasks
    assert "t-terminal" not in app.runtime.tasks
    app.runtime.scheduler.cancel(running, skip_delivery=True)
    await running.wait_terminal()


@pytest.mark.asyncio
async def test_manual_terminal_task_expires_after_ttl(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = Clock()
    app.runtime.tasks.terminal_records.now = clock
    record = register_shell_task(
        app.runtime,
        name="ttl",
        shell="echo ttl",
        intro="ttl",
        owner="actor:amy:conv:c1",
        workspace=workspace,
        delivery="manual",
        ttl_s=5,
    )
    await record.wait_terminal()

    assert record.id in app.runtime.tasks
    clock.advance(4.9)
    assert record.id in app.runtime.tasks
    clock.advance(0.1)
    assert record.id not in app.runtime.tasks


@pytest.mark.asyncio
async def test_large_terminal_stdout_evicted_after_protection_expires(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    clock = Clock()
    app.runtime.tasks.terminal_records.now = clock
    app.runtime.tasks.terminal_records.max_size_bytes = 10
    record = RuntimeTaskRecord(
        id="t-large",
        owner="actor:amy:conv:c1",
        kind="shell",
        name="large",
        status="done",
        delivery="actor",
        delivery_state="delivered",
    )
    record.stdout.write("x" * 1024)

    app.runtime.tasks.mark_terminal(record)
    assert record.id in app.runtime.tasks

    clock.advance(61)
    assert record.id not in app.runtime.tasks


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
        delivery="conversation",
    )
    await record.wait_terminal()
    await schedule_task_delivery(app.runtime, record)
    assert record.delivery_state == "pending"
    assert app.runtime.task_delivery_queue._pending.get("busy-c1") == [record.id]
    conversation._running = False
    await app.runtime.drain_pending_task_deliveries("busy-c1")
    assert str(record.delivery_state) == "delivered"


@pytest.mark.asyncio
async def test_suppressed_conversation_delivery_skips_pending_and_future_tasks(tmp_path: Path) -> None:
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
    conversation = await app.runtime.conversations.get_or_create(actor, "stop-c1")
    conversation._running = True
    queued = register_shell_task(
        app.runtime,
        name="queued",
        shell="true",
        intro="queued",
        owner="actor:amy:conv:stop-c1",
        workspace=Path(actor.config.workspace),
        delivery="conversation",
    )
    await queued.wait_terminal()
    await schedule_task_delivery(app.runtime, queued)
    assert app.runtime.task_delivery_queue._pending.get("stop-c1") == [queued.id]

    app.runtime.suppress_task_deliveries("stop-c1")
    assert queued.delivery_state == "skipped"
    assert app.runtime.task_delivery_queue._pending.get("stop-c1") is None

    future = register_shell_task(
        app.runtime,
        name="future",
        shell="true",
        intro="future",
        owner="actor:amy:conv:stop-c1",
        workspace=Path(actor.config.workspace),
        delivery="conversation",
    )
    await future.wait_terminal()
    await schedule_task_delivery(app.runtime, future)
    assert future.delivery_state == "skipped"


def test_task_delivery_suppression_expires() -> None:
    clock = Clock()
    queue = TaskDeliveryQueue(now=clock)

    assert queue.suppress("stop-c1") == []
    assert queue.is_suppressed("stop-c1") is True

    clock.advance(DEFAULT_TASK_DELIVERY_SUPPRESSION_TTL_S)

    assert queue.is_suppressed("stop-c1") is False
    assert queue._suppressed == {}


@pytest.mark.asyncio
async def test_wait_until_terminal_or_idle_returns_terminal(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        name="fast",
        shell="echo terminal-outcome",
        intro="terminal",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    outcome = await wait_until_terminal_or_idle(record, idle_s=10.0, hard_timeout_s=30.0)
    assert outcome == "terminal"
    assert record.status == "done"


@pytest.mark.asyncio
async def test_wait_until_terminal_or_idle_returns_idle(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        name="sleepy",
        shell="sleep 30",
        intro="idle",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    outcome = await wait_until_terminal_or_idle(record, idle_s=0.2, hard_timeout_s=30.0)
    assert outcome == "idle"
    assert record.status == "running"


@pytest.mark.asyncio
async def test_wait_until_terminal_or_idle_returns_timeout(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        name="chatty",
        shell="while true; do echo tick; sleep 0.05; done",
        intro="timeout",
        owner="actor:amy:conv:c1",
        workspace=workspace,
    )
    outcome = await wait_until_terminal_or_idle(record, idle_s=10.0, hard_timeout_s=0.3)
    assert outcome == "timeout"
    assert record.status == "running"


@pytest.mark.asyncio
async def test_manual_delivery_skips_delivery(tmp_path: Path) -> None:
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
    conversation = await app.runtime.conversations.get_or_create(actor, "skip-c1")
    record = register_shell_task(
        app.runtime,
        name="bg",
        shell="true",
        intro="no delivery",
        owner="actor:amy:conv:skip-c1",
        workspace=Path(actor.config.workspace),
        delivery="manual",
    )
    await record.wait_terminal()
    listener = TaskDeliveryListener(app.runtime)
    await listener.on_event(
        "task.finished",
        {
            "task_id": record.id,
            "owner": record.owner,
            "kind": "shell",
            "status": record.status,
            "error": record.error,
            "exit_code": record.exit_code,
        },
    )
    assert record.delivery_state == "skipped"
    conversation._running = False
    record.delivery = "conversation"
    record.delivery_state = "pending"
    await schedule_task_delivery(app.runtime, record)
    assert str(record.delivery_state) == "delivered"
