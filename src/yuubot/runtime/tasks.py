"""Runtime task registry, scheduler, shell runner, and task delivery."""

from __future__ import annotations

import asyncio
import builtins
import logging
import math
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import msgspec
from attrs import define, field

from .event_payloads import EmitFn, TaskFinishedPayload, TaskStartedPayload
from .expiring_index import DEFAULT_MAX_SIZE_BYTES, ExpiringIndex
from .pty_runner import run_pty_process
from .streams import TaskCoroFactory, TextStream
from .wakeup import WakeupPayload, WakeupTarget

if TYPE_CHECKING:
    from .core import Runtime

_log = logging.getLogger(__name__)

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
DeliveryState = Literal["pending", "queued", "delivered", "skipped"]
TaskDelivery = Literal["manual", "conversation", "actor"]
MAX_MANUAL_TASK_TTL_S = 3600.0
DEFAULT_MANUAL_TASK_TTL_S = MAX_MANUAL_TASK_TTL_S
DELIVERED_TASK_MIN_RETENTION_S = 60.0
PENDING_DELIVERY_TASK_RETENTION_S = 3600.0
DEFAULT_TASK_DELIVERY_SUPPRESSION_TTL_S = 3600.0


def make_owner(actor_id: str, conversation_id: str) -> str:
    return f"actor:{actor_id}:conv:{conversation_id}"


def parse_owner(owner: str) -> tuple[str, str]:
    actor_part, conversation_id = owner.split(":conv:", 1)
    return actor_part.removeprefix("actor:"), conversation_id


def new_task_id() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class TaskNotRunningError(RuntimeError):
    pass


class TaskSnapshot(msgspec.Struct, frozen=True):
    id: str
    owner: str
    kind: str
    name: str
    intro: str
    status: str
    error: str | None
    exit_code: int | None
    delivery: TaskDelivery
    delivery_state: str
    interactive: bool = True
    stdout_tail: str = ""
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None


@define
class RuntimeTaskRecord:
    id: str
    owner: str
    kind: str
    name: str = ""
    intro: str = ""
    shell: str = ""
    status: TaskStatus = "pending"
    interactive: bool = True
    stdin: TextStream = field(factory=TextStream)
    stdout: TextStream = field(factory=TextStream)
    error: str | None = None
    result: object | None = None
    exit_code: int | None = None
    delivery: TaskDelivery = "manual"
    delivery_state: DeliveryState = "pending"
    ttl_s: float | None = None
    created_at: str = field(factory=_iso_now)
    started_at: str | None = None
    finished_at: str | None = None
    _terminal: asyncio.Event = field(factory=asyncio.Event, init=False)

    def is_terminal(self) -> bool:
        return self.status in {"done", "failed", "cancelled"}

    async def wait_terminal(self) -> None:
        if self.is_terminal():
            return
        await self._terminal.wait()

    def mark_terminal(self) -> None:
        self._terminal.set()


def normalize_task_ttl(
    delivery: TaskDelivery,
    ttl_s: float | None,
    require_manual_ttl: bool,
) -> float | None:
    if ttl_s is not None:
        if not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("ttl_s must be greater than 0")
        if ttl_s > MAX_MANUAL_TASK_TTL_S:
            raise ValueError("ttl_s must be <= 3600")
    if delivery == "manual":
        if ttl_s is None:
            if require_manual_ttl:
                raise ValueError("manual task submit requires ttl_s")
            return DEFAULT_MANUAL_TASK_TTL_S
        return ttl_s
    return None


def _text_stream_size(stream: TextStream) -> int:
    return sum(len(chunk.encode("utf-8")) for chunk in stream.chunks)


def _object_size(value: object | None) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes):
        return len(value)
    try:
        return len(msgspec.json.encode(value))
    except TypeError:
        return len(repr(value).encode("utf-8"))


def task_record_size_bytes(record: RuntimeTaskRecord) -> int:
    lightweight = "|".join(
        [
            record.id,
            record.owner,
            record.kind,
            record.name,
            record.intro,
            record.shell,
            record.status,
            record.delivery,
            record.delivery_state,
            record.created_at,
            record.started_at or "",
            record.finished_at or "",
        ]
    )
    return (
        len(lightweight.encode("utf-8"))
        + _text_stream_size(record.stdin)
        + _text_stream_size(record.stdout)
        + _object_size(record.error)
        + _object_size(record.result)
    )


def _terminal_retention(record: RuntimeTaskRecord, now: float) -> tuple[float, float | None]:
    if record.delivery == "manual":
        ttl_s = record.ttl_s if record.ttl_s is not None else DEFAULT_MANUAL_TASK_TTL_S
        expires_at = now + ttl_s
        return expires_at, expires_at
    if record.delivery_state in {"pending", "queued"}:
        expires_at = now + PENDING_DELIVERY_TASK_RETENTION_S
        return expires_at, expires_at
    return now + DELIVERED_TASK_MIN_RETENTION_S, None


@define
class TaskRegistry:
    terminal_records: ExpiringIndex[RuntimeTaskRecord] = field(
        factory=lambda: ExpiringIndex(DEFAULT_MAX_SIZE_BYTES, task_record_size_bytes)
    )
    _records: dict[str, RuntimeTaskRecord] = field(factory=dict)

    def put(self, record: RuntimeTaskRecord) -> None:
        self._records[record.id] = record

    def get(self, task_id: str) -> RuntimeTaskRecord:
        record = self._records.get(task_id)
        if record is not None:
            return record
        return self.terminal_records.get(task_id)

    def __contains__(self, task_id: str) -> bool:
        return task_id in self._records or task_id in self.terminal_records

    def ids(self) -> builtins.list[str]:
        return [*self._records, *self.terminal_records.keys()]

    def mark_terminal(self, record: RuntimeTaskRecord) -> None:
        self._records.pop(record.id, None)
        now = self.terminal_records.now()
        min_retain_until, expires_at = _terminal_retention(record, now)
        self.terminal_records.put(record.id, record, min_retain_until=min_retain_until, expires_at=expires_at)

    def refresh_terminal_retention(self, record: RuntimeTaskRecord) -> None:
        if record.id not in self.terminal_records:
            return
        if record.delivery == "manual":
            return
        now = self.terminal_records.now()
        min_retain_until, expires_at = _terminal_retention(record, now)
        self.terminal_records.update_retention(record.id, min_retain_until, expires_at)

    def list(
        self,
        owner: str | None = None,
        name_glob: str = "",
    ) -> builtins.list[RuntimeTaskRecord]:
        items = [*self._records.values(), *self.terminal_records.values()]
        if owner is not None:
            items = [record for record in items if record.owner == owner]
        if name_glob:
            items = [record for record in items if fnmatch(record.name, name_glob)]
        return sorted(items, key=lambda record: record.id)


@define
class TaskScheduler:
    """Creates managed asyncio tasks, emits task.* events, and owns terminal transitions."""

    emit: EmitFn
    registry: TaskRegistry
    on_terminal: Callable[[RuntimeTaskRecord], None] | None = None
    _asyncio_tasks: dict[str, asyncio.Task[object]] = field(factory=dict)

    def schedule(self, record: RuntimeTaskRecord, coro_factory: TaskCoroFactory) -> None:
        if record.id in self._asyncio_tasks:
            raise ValueError(f"task already scheduled: {record.id}")
        record.status = "running"
        record.started_at = _iso_now()
        _log.info(
            "task scheduled task_id=%s owner=%s kind=%s name=%s delivery=%s",
            record.id,
            record.owner,
            record.kind,
            record.name,
            record.delivery,
        )
        self.emit(
            TaskStartedPayload(
                record.id,
                record.owner,
                record.kind,
                record.name,
            )
        )
        asyncio_task = asyncio.create_task(self._run(record, coro_factory))
        self._asyncio_tasks[record.id] = asyncio_task
        asyncio_task.add_done_callback(lambda task: self._on_task_done(record, task))

    def cancel(self, record: RuntimeTaskRecord, skip_delivery: bool = False) -> None:
        if skip_delivery and record.delivery_state in {"pending", "queued"}:
            record.delivery_state = "skipped"
        asyncio_task = self._asyncio_tasks.get(record.id)
        if asyncio_task is not None and not asyncio_task.done():
            _log.info(
                "task cancelling task_id=%s owner=%s kind=%s name=%s skip_delivery=%s",
                record.id,
                record.owner,
                record.kind,
                record.name,
                skip_delivery,
            )
            asyncio_task.cancel()

    async def shutdown(self) -> None:
        running = [record for record in self.registry.list() if record.status in {"pending", "running"}]
        if running:
            _log.info("task scheduler shutdown cancelling count=%s", len(running))
        for record in self.registry.list():
            if record.status in {"pending", "running"}:
                self.cancel(record, skip_delivery=True)
        if self._asyncio_tasks:
            await asyncio.gather(*self._asyncio_tasks.values(), return_exceptions=True)
        self._asyncio_tasks.clear()

    def cancel_for_owner_prefix(
        self,
        owner_prefix: str,
        skip_delivery: bool,
    ) -> None:
        for record in self.registry.list():
            if not record.owner.startswith(owner_prefix):
                continue
            if record.status in {"pending", "running"}:
                self.cancel(record, skip_delivery=skip_delivery)

    async def _run(self, record: RuntimeTaskRecord, coro_factory: TaskCoroFactory) -> object:
        try:
            record.result = await coro_factory(record.stdin, record.stdout)
            if record.kind == "shell" and record.exit_code is None:
                record.exit_code = int(record.result) if isinstance(record.result, int) else 0
            record.status = "done"
            return record.result
        except asyncio.CancelledError:
            if record.status != "failed":
                record.status = "cancelled"
            raise
        except Exception as exc:
            record.error = str(exc)
            record.status = "failed"
            _log.exception(
                "task failed task_id=%s owner=%s kind=%s name=%s",
                record.id,
                record.owner,
                record.kind,
                record.name,
            )
            return None

    def _on_task_done(self, record: RuntimeTaskRecord, asyncio_task: asyncio.Task[object]) -> None:
        self._asyncio_tasks.pop(record.id, None)
        if asyncio_task.cancelled() and record.status not in {"failed", "done"}:
            record.status = "cancelled"
        elif asyncio_task.exception() is not None and record.status != "cancelled":
            record.error = str(asyncio_task.exception())
            record.status = "failed"
        record.finished_at = _iso_now()
        record.mark_terminal()
        self.registry.mark_terminal(record)
        _log.info(
            "task finished task_id=%s owner=%s kind=%s name=%s status=%s exit_code=%s delivery=%s delivery_state=%s error=%s",
            record.id,
            record.owner,
            record.kind,
            record.name,
            record.status,
            record.exit_code,
            record.delivery,
            record.delivery_state,
            record.error,
        )
        self.emit(
            TaskFinishedPayload(
                record.id,
                record.owner,
                record.kind,
                record.status,
                record.error,
                record.exit_code,
            )
        )
        if self.on_terminal is not None:
            self.on_terminal(record)


def shell_coro_factory(shell: str, workspace: Path, tmp_dir: Path) -> TaskCoroFactory:
    async def run(stdin: TextStream, stdout: TextStream) -> int:
        env = os.environ.copy()
        env["TMPDIR"] = str(tmp_dir)
        return await run_pty_process(
            ["bash", "-lc", shell],
            workspace,
            env,
            stdin,
            stdout,
        )

    return run


def write_task_stdin(record: RuntimeTaskRecord, text: str) -> None:
    if record.status != "running":
        raise TaskNotRunningError(f"task is not running: {record.status}")
    record.stdin.write(text)


def register_shell_task(
    runtime: Runtime,
    name: str,
    shell: str,
    intro: str,
    owner: str,
    workspace: Path,
    delivery: TaskDelivery = "manual",
    ttl_s: float | None = None,
) -> RuntimeTaskRecord:
    ttl_s = normalize_task_ttl(delivery, ttl_s, False)
    _log.info(
        "shell task registering owner=%s name=%s workspace=%s delivery=%s ttl_s=%s",
        owner,
        name,
        workspace,
        delivery,
        ttl_s,
    )
    record = RuntimeTaskRecord(
        new_task_id(),
        owner,
        "shell",
        name,
        intro,
        shell,
        interactive=True,
        delivery=delivery,
        ttl_s=ttl_s,
    )
    runtime.tasks.put(record)
    runtime.scheduler.schedule(
        record,
        shell_coro_factory(shell, workspace, runtime.tmp_dir),
    )
    return record


async def wait_until_terminal_or_timeout(
    registry: TaskRegistry,
    task_id: str,
    timeout: float,
) -> None:
    record = registry.get(task_id)
    if record.is_terminal():
        return
    try:
        await asyncio.wait_for(record.wait_terminal(), timeout=timeout)
    except TimeoutError:
        return


async def wait_until_terminal_or_idle(
    record: RuntimeTaskRecord,
    idle_s: float,
    hard_timeout_s: float,
) -> Literal["terminal", "idle", "timeout"]:
    started_at = time.monotonic()
    deadline = started_at + hard_timeout_s
    while True:
        if record.is_terminal():
            return "terminal"
        now = time.monotonic()
        if now >= deadline:
            return "timeout"
        if now - record.stdout.updated_at >= idle_s:
            return "idle"
        remaining_idle = idle_s - (now - record.stdout.updated_at)
        remaining_hard = deadline - now
        wait_for = min(remaining_idle, remaining_hard, 0.05)
        await record.stdout.await_next(wait_for)


def format_task_delivery(record: RuntimeTaskRecord) -> str:
    lines = [f"Task '{record.name}' finished with status {record.status}."]
    if record.intro:
        lines.append(record.intro)
    if record.error:
        lines.append(f"Error: {record.error}")
    output = record.stdout.tail(max_bytes=65536)
    if output:
        lines.append("Output:")
        lines.append(output)
    if record.exit_code is not None:
        lines.append(f"Exit code: {record.exit_code}")
    return "\n".join(lines)


async def deliver_task_result(runtime: Runtime, record: RuntimeTaskRecord) -> None:
    actor_id, conversation_id = parse_owner(record.owner)
    text = format_task_delivery(record)
    _log.info(
        "task delivery sending task_id=%s owner=%s actor_id=%s conversation_id=%s delivery=%s status=%s",
        record.id,
        record.owner,
        actor_id,
        conversation_id,
        record.delivery,
        record.status,
    )
    target = (
        WakeupTarget("actor_inbound", actor_id, None)
        if record.delivery == "actor"
        else WakeupTarget("task_delivery", actor_id, conversation_id)
    )
    await runtime.wakeup.deliver(
        target,
        WakeupPayload(
            text,
            {"task_id": record.id, "task_name": record.name, "status": record.status, "task_delivery": record.delivery},
        ),
    )
    record.delivery_state = "delivered"
    runtime.tasks.refresh_terminal_retention(record)
    _log.info(
        "task delivery delivered task_id=%s owner=%s delivery_state=%s",
        record.id,
        record.owner,
        record.delivery_state,
    )


async def schedule_task_delivery(runtime: Runtime, record: RuntimeTaskRecord) -> None:
    if record.delivery_state != "pending":
        return
    if record.delivery == "manual":
        record.delivery_state = "skipped"
        runtime.tasks.refresh_terminal_retention(record)
        _log.info("task delivery skipped manual task_id=%s owner=%s", record.id, record.owner)
        return
    _, conversation_id = parse_owner(record.owner)
    conversation = runtime.conversations.get_if_present(conversation_id)
    if (
        record.delivery == "conversation"
        and conversation is not None
        and conversation.task_deliveries_suppressed(now=time.monotonic())
    ):
        record.delivery_state = "skipped"
        runtime.tasks.refresh_terminal_retention(record)
        _log.info(
            "task delivery skipped suppressed task_id=%s owner=%s conversation_id=%s",
            record.id,
            record.owner,
            conversation_id,
        )
        return
    if record.delivery == "conversation" and conversation is not None and conversation.running:
        conversation.queue_task_delivery(record.id)
        record.delivery_state = "queued"
        _log.info(
            "task delivery queued task_id=%s owner=%s conversation_id=%s",
            record.id,
            record.owner,
            conversation_id,
        )
        return
    await deliver_task_result(runtime, record)


async def drain_pending_task_deliveries(runtime: Runtime, conversation_id: str) -> None:
    conversation = runtime.conversations.get_if_present(conversation_id)
    task_ids = conversation.pop_task_deliveries() if conversation is not None else []
    for task_id in task_ids:
        if task_id not in runtime.tasks:
            continue
        record = runtime.tasks.get(task_id)
        if record.delivery_state not in {"pending", "queued"}:
            continue
        if record.status == "cancelled":
            record.delivery_state = "skipped"
            runtime.tasks.refresh_terminal_retention(record)
            _log.info("queued task delivery skipped cancelled task_id=%s", record.id)
            continue
        try:
            await deliver_task_result(runtime, record)
        except Exception:
            _log.exception("queued task delivery failed for %s", record.id)
            record.delivery_state = "skipped"
            runtime.tasks.refresh_terminal_retention(record)


def suppress_conversation_task_deliveries(runtime: Runtime, conversation_id: str) -> None:
    conversation = runtime.conversations.get_if_present(conversation_id)
    task_ids = (
        conversation.suppress_task_deliveries(
            now=time.monotonic(),
            ttl_s=DEFAULT_TASK_DELIVERY_SUPPRESSION_TTL_S,
        )
        if conversation is not None
        else []
    )
    _log.info(
        "conversation task deliveries suppressed conversation_id=%s queued_count=%s",
        conversation_id,
        len(task_ids),
    )
    skip_conversation_task_deliveries(runtime, conversation_id, task_ids)


def skip_conversation_task_deliveries(
    runtime: Runtime,
    conversation_id: str,
    task_ids: list[str] | tuple[str, ...] = (),
) -> None:
    for task_id in task_ids:
        if task_id in runtime.tasks:
            record = runtime.tasks.get(task_id)
            if record.delivery_state in {"pending", "queued"}:
                record.delivery_state = "skipped"
                runtime.tasks.refresh_terminal_retention(record)
                _log.info(
                    "conversation task delivery skipped task_id=%s conversation_id=%s",
                    record.id,
                    conversation_id,
                )
    owner = f":conv:{conversation_id}"
    for record in runtime.tasks.list():
        if record.delivery == "conversation" and owner in record.owner and record.delivery_state in {"pending", "queued"}:
            record.delivery_state = "skipped"
            runtime.tasks.refresh_terminal_retention(record)


def task_record_snapshot(record: RuntimeTaskRecord, include_stdout: bool = False) -> TaskSnapshot:
    return TaskSnapshot(
        record.id,
        record.owner,
        record.kind,
        record.name,
        record.intro,
        record.status,
        record.error,
        record.exit_code,
        record.delivery,
        record.delivery_state,
        record.interactive,
        record.stdout.tail(max_bytes=65536) if include_stdout else "",
        record.created_at,
        record.started_at,
        record.finished_at,
    )
