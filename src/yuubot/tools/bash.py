"""Bash tool backed by runtime PTY shell tasks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar, Final, Literal, cast

import msgspec
from attrs import define, field

from ..domain.messages import ConversationContext
from ..runtime.core import Runtime
from ..runtime.pty_display import filter_tool_output, forward_pty_snapshots
from ..runtime.tasks import (
    register_shell_task,
    wait_until_terminal_or_idle,
    make_owner,
)
from .base import ToolConfig, ToolSpec
from .progress import current_progress

MAX_OUTPUT_BYTES: Final[int] = 1024 * 1024
DEFAULT_IDLE_TIMEOUT_S: Final[float] = 10.0
HARD_TIMEOUT_S: Final[float] = 235.0

DESCRIPTION = """Run a bash command with the actor workspace as the working directory.

The command runs as `bash -lc <command>` in a PTY-backed shell task. Output streams to the tool progress channel. Fast commands return synchronously with exit code and stdout. Returned stdout is capped at 1 MiB and includes the omitted byte range when truncated. When stdout is silent for `idle_timeout_s` (default 10) or the hard ceiling (~235s) is reached, the task detaches and continues under Runtime; the result includes `task_id` and current output.

Query detached tasks with `await yb.tasks.find(task_id)`, `await task.output()`, `await task.write(text)`, and `await task.cancel()` (see Integration SDKs).

Use for shell-native work such as git, package installation, or blocking CLI tools. Prefer `execute_python` for orchestration and integration facade calls."""


class BashPayload(msgspec.Struct, frozen=True):
    command: str
    idle_timeout_s: float | None = None


def _format_sync_result(record: object) -> str:
    from ..runtime.tasks import RuntimeTaskRecord

    task = cast(RuntimeTaskRecord, record)
    output = filter_tool_output(task.stdout.tail_with_notice(MAX_OUTPUT_BYTES)).rstrip()
    lines = [f"exit_code: {task.exit_code if task.exit_code is not None else 0}"]
    if output:
        lines.extend(["stdout:", output])
    return "\n".join(lines)


def _format_detach_result(record: object, reason: Literal["idle", "timeout"]) -> str:
    from ..runtime.tasks import RuntimeTaskRecord

    task = cast(RuntimeTaskRecord, record)
    output = filter_tool_output(task.stdout.tail_with_notice(MAX_OUTPUT_BYTES)).rstrip()
    reason_label = "stdout idle" if reason == "idle" else "hard timeout"
    lines = [
        "detached: true",
        f"reason: {reason_label}",
        f"task_id: {task.id}",
        f"status: {task.status}",
    ]
    if output:
        lines.extend(["output:", output])
    lines.extend(
        [
            "The shell task continues under Runtime and will not automatically continue this conversation.",
            "Its terminal output is retained for up to 1 hour as an expiring offload buffer, not durable storage.",
            "Query with `await yb.tasks.find(task_id)`, `await task.output()`, `await task.write(text)`, and `await task.cancel()`.",
        ]
    )
    return "\n".join(lines)


@define
class BashTool:
    payload_type: ClassVar[type[msgspec.Struct]] = BashPayload

    runtime: Runtime
    workspace: Path
    owner: str
    _forward_task: asyncio.Task[None] | None = field(default=None, init=False)

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        data = cast(BashPayload, payload)
        idle_s = data.idle_timeout_s if data.idle_timeout_s is not None else DEFAULT_IDLE_TIMEOUT_S
        record = register_shell_task(
            self.runtime,
            "bash",
            data.command,
            f"bash: {data.command[:200]}",
            self.owner,
            self.workspace,
            "manual",
            3600,
        )
        self._forward_task = asyncio.create_task(self._forward_stdout(record))
        try:
            outcome = await wait_until_terminal_or_idle(record, idle_s, HARD_TIMEOUT_S)
        finally:
            if self._forward_task is not None:
                self._forward_task.cancel()
                await asyncio.gather(self._forward_task, return_exceptions=True)
                self._forward_task = None
        if outcome == "terminal":
            record.delivery_state = "skipped"
            return _format_sync_result(record)
        return _format_detach_result(record, outcome)

    async def close(self) -> None:
        if self._forward_task is not None:
            self._forward_task.cancel()
            await asyncio.gather(self._forward_task, return_exceptions=True)
            self._forward_task = None

    async def _forward_stdout(self, record: object) -> None:
        from ..runtime.tasks import RuntimeTaskRecord

        task = cast(RuntimeTaskRecord, record)
        progress = current_progress()
        if progress is None:
            return
        await forward_pty_snapshots(task.stdout, progress.replace)


def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> BashTool:
    del config
    return BashTool(
        runtime=runtime,
        workspace=context.workspace.resolve(),
        owner=make_owner(context.actor, context.conversation_id),
    )


BASH_SPEC = ToolSpec(BashPayload, DESCRIPTION, _factory)
