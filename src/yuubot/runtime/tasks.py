"""Runtime task registry, scheduler, shell runner, and task delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import msgspec
from attrs import define, field

from .streams import TaskCoroFactory, TextStream
from .wakeup import WakeupPayload, WakeupTarget

if TYPE_CHECKING:
    from .core import Runtime

_log = logging.getLogger(__name__)

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
DeliveryState = Literal["pending", "delivered", "skipped"]
EmitFn = Callable[..., None]


def make_owner(*, actor_id: str, conversation_id: str) -> str:
    return f"actor:{actor_id}:conv:{conversation_id}"


def parse_owner(owner: str) -> tuple[str, str]:
    actor_part, conversation_id = owner.split(":conv:", 1)
    return actor_part.removeprefix("actor:"), conversation_id


def new_task_id() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


class TaskSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    owner: str
    kind: str
    name: str
    intro: str
    status: str
    error: str | None
    exit_code: int | None
    delivery_state: str
    stdout_tail: str = ""


@define
class RuntimeTaskRecord:
    id: str
    owner: str
    kind: str
    name: str = ""
    intro: str = ""
    shell: str = ""
    status: TaskStatus = "pending"
    stdin: TextStream = field(factory=TextStream)
    stdout: TextStream = field(factory=TextStream)
    error: str | None = None
    result: object | None = None
    exit_code: int | None = None
    delivery_state: DeliveryState = "pending"
    _terminal: asyncio.Event = field(factory=asyncio.Event, init=False)

    def is_terminal(self) -> bool:
        return self.status in {"done", "failed", "cancelled"}

    async def wait_terminal(self) -> None:
        if self.is_terminal():
            return
        await self._terminal.wait()

    def mark_terminal(self) -> None:
        self._terminal.set()


@define
class TaskRegistry:
    _records: dict[str, RuntimeTaskRecord] = field(factory=dict)

    def put(self, record: RuntimeTaskRecord) -> None:
        self._records[record.id] = record

    def get(self, task_id: str) -> RuntimeTaskRecord:
        return self._records[task_id]

    def __contains__(self, task_id: str) -> bool:
        return task_id in self._records

    def ids(self) -> list[str]:
        return list(self._records)

    def list(
        self,
        *,
        owner: str | None = None,
        name_glob: str = "",
    ) -> list[RuntimeTaskRecord]:
        items = list(self._records.values())
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
    _asyncio_tasks: dict[str, asyncio.Task[object]] = field(factory=dict)

    def schedule(self, record: RuntimeTaskRecord, coro_factory: TaskCoroFactory) -> None:
        if record.id in self._asyncio_tasks:
            raise ValueError(f"task already scheduled: {record.id}")
        record.status = "running"
        self.emit(
            "task.started",
            task_id=record.id,
            owner=record.owner,
            kind=record.kind,
            name=record.name,
        )
        asyncio_task = asyncio.create_task(self._run(record, coro_factory))
        self._asyncio_tasks[record.id] = asyncio_task
        asyncio_task.add_done_callback(lambda task: self._on_task_done(record, task))

    def cancel(self, record: RuntimeTaskRecord, *, skip_delivery: bool = False) -> None:
        if skip_delivery and record.delivery_state == "pending":
            record.delivery_state = "skipped"
        asyncio_task = self._asyncio_tasks.get(record.id)
        if asyncio_task is not None and not asyncio_task.done():
            asyncio_task.cancel()

    async def shutdown(self) -> None:
        for record in self.registry.list():
            if record.status in {"pending", "running"}:
                self.cancel(record, skip_delivery=True)
        if self._asyncio_tasks:
            await asyncio.gather(*self._asyncio_tasks.values(), return_exceptions=True)
        self._asyncio_tasks.clear()

    def cancel_for_owner_prefix(
        self,
        owner_prefix: str,
        *,
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
            return None

    def _on_task_done(self, record: RuntimeTaskRecord, asyncio_task: asyncio.Task[object]) -> None:
        self._asyncio_tasks.pop(record.id, None)
        if asyncio_task.cancelled() and record.status not in {"failed", "done"}:
            record.status = "cancelled"
        elif asyncio_task.exception() is not None and record.status != "cancelled":
            record.error = str(asyncio_task.exception())
            record.status = "failed"
        record.mark_terminal()
        self.emit(
            "task.finished",
            task_id=record.id,
            owner=record.owner,
            kind=record.kind,
            status=record.status,
            error=record.error,
            exit_code=record.exit_code,
        )


async def _terminate_shell_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        pass


def shell_coro_factory(*, shell: str, workspace: Path) -> TaskCoroFactory:
    async def run(_stdin: TextStream, stdout: TextStream) -> int:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            shell,
            cwd=workspace,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def pump(stream: asyncio.StreamReader, chunks: list[bytes], mirror: bool) -> None:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if mirror:
                    stdout.write(chunk.decode("utf-8", errors="replace"))

        try:
            await asyncio.gather(
                pump(proc.stdout, stdout_chunks, True),
                pump(proc.stderr, stderr_chunks, False),
            )
            code = await proc.wait()
        except asyncio.CancelledError:
            await _terminate_shell_process(proc)
            raise
        if stderr_chunks:
            stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            if stderr_text.strip():
                stdout.write(stderr_text if stdout_chunks else stderr_text)
        return code

    return run


def register_shell_task(
    runtime: Runtime,
    *,
    name: str,
    shell: str,
    intro: str,
    owner: str,
    workspace: Path,
) -> RuntimeTaskRecord:
    record = RuntimeTaskRecord(
        id=new_task_id(),
        owner=owner,
        kind="shell",
        name=name,
        intro=intro,
        shell=shell,
    )
    runtime.tasks.put(record)
    runtime.scheduler.schedule(record, shell_coro_factory(shell=shell, workspace=workspace))
    return record


async def wait_until_terminal_or_timeout(
    registry: TaskRegistry,
    task_id: str,
    *,
    timeout: float,
) -> None:
    record = registry.get(task_id)
    if record.is_terminal():
        return
    try:
        await asyncio.wait_for(record.wait_terminal(), timeout=timeout)
    except TimeoutError:
        return


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
    await runtime.wakeup.deliver(
        WakeupTarget(kind="task_delivery", actor_id=actor_id, conversation_id=conversation_id),
        WakeupPayload(
            text=text,
            source={"task_id": record.id, "task_name": record.name, "status": record.status},
        ),
    )
    record.delivery_state = "delivered"


@define
class TaskDeliveryListener:
    _runtime: Runtime

    async def on_event(self, kind: str, payload: dict[str, object]) -> None:
        if kind != "task.finished":
            return
        if payload.get("kind") != "shell":
            return
        owner = payload.get("owner")
        if not isinstance(owner, str) or ":conv:" not in owner:
            return
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or task_id not in self._runtime.tasks:
            return
        record = self._runtime.tasks.get(task_id)
        if record.delivery_state != "pending":
            return
        if record.status == "cancelled":
            record.delivery_state = "skipped"
            return
        try:
            await deliver_task_result(self._runtime, record)
        except Exception:
            _log.exception("task delivery failed for %s", record.id)
            record.delivery_state = "skipped"


def task_record_snapshot(record: RuntimeTaskRecord, *, include_stdout: bool = False) -> TaskSnapshot:
    return TaskSnapshot(
        id=record.id,
        owner=record.owner,
        kind=record.kind,
        name=record.name,
        intro=record.intro,
        status=record.status,
        error=record.error,
        exit_code=record.exit_code,
        delivery_state=record.delivery_state,
        stdout_tail=record.stdout.tail(max_bytes=65536) if include_stdout else "",
    )
