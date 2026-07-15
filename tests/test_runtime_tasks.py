from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from yuubot.app import Yuubot
from yuubot.domain import ConversationContext, LLMInput, StreamEvent, StreamStopPayload, TextDeltaPayload
from yuubot.llm import scripted_reply
from yuubot.runtime.cache import CachePool
from yuubot.runtime.tasks import (
    CURRENT_RUNTIME_TASK_ID,
    PENDING_DELIVERY_TASK_RETENTION_S,
    RuntimeTaskRecord,
    TaskNotRunningError,
    register_shell_task,
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


class BlockingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache, stop_event, metadata
        self.started.set()
        await self.release.wait()
        yield StreamEvent("text-1", "text_delta", TextDeltaPayload("ok"))
        yield StreamEvent("stop", "stream_stop", StreamStopPayload("stop"))

    async def close(self) -> None:
        return None


async def wait_for_delivery_state(record: RuntimeTaskRecord, state: str) -> None:
    for _ in range(100):
        if record.delivery_state == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {record.id} did not reach delivery_state={state}, got {record.delivery_state}")


async def wait_for_pending_delivery(conversation, task_id: str) -> None:
    for _ in range(100):
        if task_id in conversation.pending_task_delivery_ids():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} was not queued for conversation delivery")


@pytest.mark.asyncio
async def test_actor_task_registry_clears_completed_task_and_allows_restart(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    first_done = asyncio.Event()
    second_done = asyncio.Event()

    async def first_run(_stdin, _stdout) -> None:
        first_done.set()

    async def second_run(_stdin, _stdout) -> None:
        second_done.set()

    try:
        app.runtime.start_actor_task("amy", first_run)
        await first_done.wait()
        for _ in range(100):
            if "actor:amy" not in app.runtime._actor_tasks:
                break
            await asyncio.sleep(0.01)
        assert "actor:amy" not in app.runtime._actor_tasks

        app.runtime.start_actor_task("amy", second_run)
        await second_done.wait()
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_shell_task_runs_in_pty_and_streams_stdout(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "echo",
        "echo hello-pty",
        "pty test",
        "actor:amy:conv:c1",
        workspace,
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
async def test_parent_waits_for_child_tree_and_exposes_relationships(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    child_release = asyncio.Event()
    child_started = asyncio.Event()
    records: list[RuntimeTaskRecord] = []

    async def child_run(_stdin, _stdout) -> str:
        child_started.set()
        await child_release.wait()
        return "child result"

    async def parent_run(_stdin, _stdout) -> str:
        parent_id = CURRENT_RUNTIME_TASK_ID.get()
        assert parent_id is not None
        child = RuntimeTaskRecord(
            "t-child",
            "actor:amy:conv:subagent:t-parent",
            "agent-work",
            "child",
            parent_task_id=parent_id,
        )
        records.append(child)
        app.runtime.tasks.put(child)
        app.runtime.scheduler.schedule(child, child_run)
        return "parent result"

    parent = RuntimeTaskRecord(
        "t-parent",
        "actor:amy:conv:c1",
        "agent",
        "parent",
    )
    app.runtime.tasks.put(parent)
    app.runtime.scheduler.schedule(parent, parent_run)
    try:
        await child_started.wait()
        for _ in range(100):
            if parent.status == "waiting_children":
                break
            await asyncio.sleep(0.01)
        child = records[0]
        assert parent.status == "waiting_children"
        assert not parent.is_terminal()
        assert child.parent_task_id == parent.id
        assert child.root_task_id == parent.id
        assert app.runtime.tasks.list(parent_task_id=parent.id) == [child]
        assert {item.id for item in app.runtime.tasks.list(root_task_id=parent.id)} == {parent.id, child.id}

        child_release.set()
        await parent.wait_terminal()
        assert child.status == "done"
        assert child.delivery_state == "delivered"
        assert parent.status == "done"
        assert "child result" in "\n".join(parent.pop_child_notices())
    finally:
        child_release.set()
        await app.shutdown()


@pytest.mark.asyncio
async def test_parent_cancel_cascades_to_child_tree(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    child_started = asyncio.Event()
    records: list[RuntimeTaskRecord] = []

    async def child_run(_stdin, _stdout) -> None:
        child_started.set()
        await asyncio.Event().wait()

    async def parent_run(_stdin, _stdout) -> None:
        parent_id = CURRENT_RUNTIME_TASK_ID.get()
        assert parent_id is not None
        child = RuntimeTaskRecord(
            "t-cancel-child",
            "actor:amy:conv:subagent:t-cancel-parent",
            "shell",
            "child",
            parent_task_id=parent_id,
        )
        records.append(child)
        app.runtime.tasks.put(child)
        app.runtime.scheduler.schedule(child, child_run)

    parent = RuntimeTaskRecord(
        "t-cancel-parent",
        "actor:amy:conv:c1",
        "agent",
        "parent",
    )
    app.runtime.tasks.put(parent)
    app.runtime.scheduler.schedule(parent, parent_run)
    try:
        await child_started.wait()
        app.runtime.scheduler.cancel(parent)
        await parent.wait_terminal()
        child = records[0]
        await child.wait_terminal()
        assert parent.status == "cancelled"
        assert child.status == "cancelled"
        assert child.delivery_state == "skipped"
    finally:
        await app.shutdown()


def test_task_registry_rejects_invalid_parent_relationships() -> None:
    from yuubot.runtime.tasks import TaskRegistry

    registry = TaskRegistry()
    parent = RuntimeTaskRecord("t-parent", "actor:amy:conv:c1", "agent")
    registry.put(parent)

    with pytest.raises(ValueError, match="different actor"):
        registry.put(
            RuntimeTaskRecord(
                "t-cross-actor",
                "actor:bob:conv:c2",
                "shell",
                parent_task_id=parent.id,
            )
        )

    parent.status = "done"
    with pytest.raises(ValueError, match="not active"):
        registry.put(
            RuntimeTaskRecord(
                "t-after-terminal",
                "actor:amy:conv:c1",
                "shell",
                parent_task_id=parent.id,
            )
        )


_READLINE_SHELL = (
    "python3 -c 'import sys; line=sys.stdin.readline(); print(f\"got:{line.strip()}\")'"
)


@pytest.mark.asyncio
async def test_shell_task_stdin_written_before_pty_subscribes(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "read-early",
        _READLINE_SHELL,
        "stdin race test",
        "actor:amy:conv:c1",
        workspace,
    )
    write_task_stdin(record, "answer\n")
    await record.wait_terminal()
    assert record.status == "done"
    assert "got:answer" in record.stdout.tail(max_bytes=1024)


@pytest.mark.asyncio
async def test_shell_task_accepts_stdin(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "read",
        _READLINE_SHELL,
        "stdin test",
        "actor:amy:conv:c1",
        workspace,
    )
    await asyncio.sleep(0)
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
        "sleep",
        "sleep 30",
        "running",
        "actor:amy:conv:c1",
        workspace,
    )
    terminal = RuntimeTaskRecord(
        "t-terminal",
        "actor:amy:conv:c1",
        "shell",
        "terminal",
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
        "ttl",
        "echo ttl",
        "ttl",
        "actor:amy:conv:c1",
        workspace,
        "manual",
        5,
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
        "t-large",
        "actor:amy:conv:c1",
        "shell",
        "large",
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
async def test_pending_delivery_terminal_task_expires_after_retention(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    clock = Clock()
    app.runtime.tasks.terminal_records.now = clock
    record = RuntimeTaskRecord(
        "t-pending",
        "actor:amy:conv:c1",
        "shell",
        "pending",
        status="done",
        delivery="conversation",
        delivery_state="pending",
    )

    app.runtime.tasks.mark_terminal(record)
    assert record.id in app.runtime.tasks

    clock.advance(PENDING_DELIVERY_TASK_RETENTION_S)
    assert record.id not in app.runtime.tasks


@pytest.mark.asyncio
async def test_write_task_stdin_rejects_terminal_task(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "done",
        "true",
        "done",
        "actor:amy:conv:c1",
        workspace,
    )
    await record.wait_terminal()
    with pytest.raises(TaskNotRunningError):
        write_task_stdin(record, "x")


@pytest.mark.asyncio
async def test_task_delivery_queues_while_conversation_busy(tmp_path: Path) -> None:
    from yuubot.actor import ActorConfig

    app = await Yuubot.create(tmp_path / "data")
    provider = BlockingProvider()
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model="fake",
        ),
        provider,
    )
    try:
        chat_task = asyncio.create_task(app.chat("amy", "first", conversation_id="busy-c1"))
        await provider.started.wait()
        conversation = app.runtime.conversations.get_if_present("busy-c1")
        assert conversation is not None
        record = register_shell_task(
            app.runtime,
            "bg",
            "true",
            "delivery queue",
            "actor:amy:conv:busy-c1",
            Path(actor.config.workspace),
            "conversation",
        )
        await record.wait_terminal()
        await wait_for_pending_delivery(conversation, record.id)
        assert record.delivery_state == "queued"

        provider.release.set()
        await chat_task
        await wait_for_delivery_state(record, "delivered")
        assert conversation.pending_task_delivery_ids() == []
    finally:
        provider.release.set()
        await app.shutdown()


@pytest.mark.asyncio
async def test_suppressed_conversation_delivery_skips_pending_and_future_tasks(tmp_path: Path) -> None:
    from yuubot.actor import ActorConfig

    app = await Yuubot.create(tmp_path / "data")
    provider = BlockingProvider()
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model="fake",
        ),
        provider,
    )
    try:
        chat_task = asyncio.create_task(app.chat("amy", "first", conversation_id="stop-c1"))
        await provider.started.wait()
        conversation = app.runtime.conversations.get_if_present("stop-c1")
        assert conversation is not None
        queued = register_shell_task(
            app.runtime,
            "queued",
            "true",
            "queued",
            "actor:amy:conv:stop-c1",
            Path(actor.config.workspace),
            "conversation",
        )
        await queued.wait_terminal()
        await wait_for_pending_delivery(conversation, queued.id)
        assert queued.delivery_state == "queued"

        app.runtime.suppress_task_deliveries("stop-c1")
        assert queued.delivery_state == "skipped"
        assert conversation.pending_task_delivery_ids() == []

        future = register_shell_task(
            app.runtime,
            "future",
            "true",
            "future",
            "actor:amy:conv:stop-c1",
            Path(actor.config.workspace),
            "conversation",
        )
        await future.wait_terminal()
        await wait_for_delivery_state(future, "skipped")

        provider.release.set()
        await chat_task
    finally:
        provider.release.set()
        await app.shutdown()


@pytest.mark.asyncio
async def test_parent_interrupt_preserves_queued_agent_task_delivery(tmp_path: Path) -> None:
    from yuubot.actor import ActorConfig

    app = await Yuubot.create(tmp_path / "data")
    provider = BlockingProvider()
    app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model="fake",
        ),
        provider,
    )
    try:
        chat_task = asyncio.create_task(app.chat("amy", "first", conversation_id="agent-interrupt-c1"))
        await provider.started.wait()
        conversation = app.runtime.conversations.get_if_present("agent-interrupt-c1")
        assert conversation is not None
        record = RuntimeTaskRecord(
            "t-agent-interrupt",
            "actor:amy:conv:agent-interrupt-c1",
            "agent",
            "reviewer:t-agent-interrupt",
            status="done",
            delivery="conversation",
            delivery_state="queued",
            metadata={"subagent": "reviewer", "model_tier": "same"},
        )
        app.runtime.tasks.put(record)
        conversation.queue_task_delivery(record.id)

        assert app.interrupt("agent-interrupt-c1")
        assert record.delivery_state == "queued"
        assert conversation.pending_task_delivery_ids() == [record.id]

        provider.release.set()
        await chat_task
        await wait_for_delivery_state(record, "delivered")
    finally:
        provider.release.set()
        await app.shutdown()


@pytest.mark.asyncio
async def test_discarded_conversation_skips_queued_task_deliveries(tmp_path: Path) -> None:
    from yuubot.actor import ActorConfig

    app = await Yuubot.create(tmp_path / "data")
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model="fake",
        ),
        scripted_reply("ok"),
    )
    try:
        conversation = await app.runtime.conversations.get_or_create(actor, "discard-c1")
        record = RuntimeTaskRecord(
            "t-queued",
            "actor:amy:conv:discard-c1",
            "shell",
            "queued",
            status="done",
            delivery="conversation",
            delivery_state="queued",
        )
        app.runtime.tasks.mark_terminal(record)
        conversation.queue_task_delivery(record.id)

        assert await app.runtime.conversations.discard("discard-c1")

        assert record.delivery_state == "skipped"
        assert conversation.pending_task_delivery_ids() == []
        assert not app.runtime.conversations.has("discard-c1")
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_wait_until_terminal_or_idle_returns_terminal(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "fast",
        "echo terminal-outcome",
        "terminal",
        "actor:amy:conv:c1",
        workspace,
    )
    outcome = await wait_until_terminal_or_idle(record, 10.0, 30.0)
    assert outcome == "terminal"
    assert record.status == "done"


@pytest.mark.asyncio
async def test_wait_until_terminal_or_idle_returns_idle(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "sleepy",
        "sleep 30",
        "idle",
        "actor:amy:conv:c1",
        workspace,
    )
    outcome = await wait_until_terminal_or_idle(record, 0.2, 30.0)
    assert outcome == "idle"
    assert record.status == "running"


@pytest.mark.asyncio
async def test_wait_until_terminal_or_idle_returns_timeout(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = register_shell_task(
        app.runtime,
        "chatty",
        "while true; do echo tick; sleep 0.05; done",
        "timeout",
        "actor:amy:conv:c1",
        workspace,
    )
    outcome = await wait_until_terminal_or_idle(record, 10.0, 0.3)
    assert outcome == "timeout"
    assert record.status == "running"


@pytest.mark.asyncio
async def test_manual_delivery_skips_delivery(tmp_path: Path) -> None:
    from yuubot.actor import ActorConfig
    from yuubot.llm import scripted_reply

    app = await Yuubot.create(tmp_path / "data")
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model="fake",
        ),
        scripted_reply("ok"),
    )
    await app.runtime.conversations.get_or_create(actor, "skip-c1")
    try:
        record = register_shell_task(
            app.runtime,
            "bg",
            "true",
            "no delivery",
            "actor:amy:conv:skip-c1",
            Path(actor.config.workspace),
            "manual",
        )
        await record.wait_terminal()
        await wait_for_delivery_state(record, "skipped")
        assert record.delivery_state == "skipped"
        record.delivery = "conversation"
        record.delivery_state = "pending"
        assert app.runtime.scheduler.on_terminal is not None
        app.runtime.scheduler.on_terminal(record)
        await wait_for_delivery_state(record, "delivered")
    finally:
        await app.shutdown()
