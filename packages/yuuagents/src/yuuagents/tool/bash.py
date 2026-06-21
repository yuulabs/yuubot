"""Workspace-scoped bash command tool."""

from __future__ import annotations

import asyncio
import os
import signal
import shutil
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar

import msgspec
import pydantic

from yuuagents.obs.entitylog import EntityLog
from yuuagents.tool.primitives import (
    Tool,
    ToolCallTask,
    ToolContext,
    ToolDefinition,
    register_tool_type,
)

_MAX_INLINE_STDOUT_CHARS = 2000
_STDOUT_HEAD_CHARS = 500
_STDOUT_TAIL_CHARS = 1500
_MAX_STDERR_CHARS = 4000


class BashToolConfig(msgspec.Struct, frozen=True):
    workspace_root: str = ""
    timeout_s: float = 30.0
    max_timeout_s: float = 120.0
    max_stderr_chars: int = _MAX_STDERR_CHARS


class BashParams(pydantic.BaseModel):
    command: str
    cwd: str | None = None
    timeout_s: float | None = None


class BashRunner:
    def __init__(
        self,
        *,
        workspace_root: Path,
        timeout_s: float,
        max_timeout_s: float,
        max_stderr_chars: int,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.timeout_s = timeout_s
        self.max_timeout_s = max_timeout_s
        self.max_stderr_chars = max_stderr_chars
        if self.timeout_s <= 0:
            raise ValueError("bash timeout_s must be positive")
        if self.max_timeout_s <= 0:
            raise ValueError("bash max_timeout_s must be positive")
        if self.timeout_s > self.max_timeout_s:
            raise ValueError("bash timeout_s must be <= max_timeout_s")
        if self.max_stderr_chars <= 0:
            raise ValueError("bash max_stderr_chars must be positive")

    @classmethod
    def from_config(cls, config: BashToolConfig) -> "BashRunner":
        if not config.workspace_root:
            raise ValueError("bash tool requires workspace_root")
        return cls(
            workspace_root=Path(config.workspace_root),
            timeout_s=config.timeout_s,
            max_timeout_s=config.max_timeout_s,
            max_stderr_chars=config.max_stderr_chars,
        )

    def resolve_cwd(self, raw_cwd: str | None) -> Path:
        if raw_cwd is None or raw_cwd == "":
            return self.workspace_root
        path = Path(raw_cwd)
        if path.is_absolute():
            raise ValueError("cwd must be relative to the workspace")
        if ".." in path.parts:
            raise ValueError("cwd must not contain '..'")
        resolved = (self.workspace_root / path).resolve()
        if (
            resolved != self.workspace_root
            and self.workspace_root not in resolved.parents
        ):
            raise ValueError(f"cwd escapes workspace: {raw_cwd!r}")
        return resolved

    def resolve_timeout(self, timeout_s: float | None) -> float:
        if timeout_s is None:
            return self.timeout_s
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if timeout_s > self.max_timeout_s:
            raise ValueError(
                f"timeout_s must be <= configured maximum {self.max_timeout_s:g}s"
            )
        return timeout_s

    async def run(
        self,
        *,
        command: str,
        cwd: str | None,
        timeout_s: float | None,
        context: ToolContext,
    ) -> str:
        if not command:
            raise ValueError("command must not be empty")
        bash_path = shutil.which("bash")
        if bash_path is None:
            raise RuntimeError("bash executable was not found on PATH")

        resolved_cwd = self.resolve_cwd(cwd)
        timeout = self.resolve_timeout(timeout_s)
        started = time.monotonic()
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        bash_args = self._bash_args(command)
        proc = await asyncio.create_subprocess_exec(
            bash_path,
            *bash_args,
            cwd=resolved_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        timed_out = False
        stdout_task = asyncio.create_task(
            _read_stream(proc.stdout, stdout_parts, context.entity_log),
        )
        stderr_task = asyncio.create_task(
            _read_stream(proc.stderr, stderr_parts, None),
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        await stdout_task
        await stderr_task

        duration_s = time.monotonic() - started
        return _render_terminal_result(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=proc.returncode,
            timed_out=timed_out,
            duration_s=duration_s,
            max_stderr_chars=self.max_stderr_chars,
        )

    def _bash_args(self, command: str) -> tuple[str, ...]:
        bashrc = Path(os.path.expanduser("~/.bashrc"))
        if bashrc.is_file():
            return (
                "--noprofile",
                "--norc",
                "-c",
                'source "$1"; shift; eval "$1"',
                "bash",
                str(bashrc),
                command,
            )
        return ("--noprofile", "--norc", "-c", command)


class BashTool(Tool[BashParams, str]):
    config_type: ClassVar[type] = BashToolConfig

    def __init__(self, runner: BashRunner) -> None:
        self._runner = runner

    @classmethod
    def from_startup(cls, runtime: Any, config: BashToolConfig) -> "BashTool":
        _ = runtime
        return cls(BashRunner.from_config(config))

    @property
    def definition(self) -> ToolDefinition[BashParams, str]:
        return ToolDefinition(
            name="bash",
            description=(
                "Run one command through initialized bash from the configured "
                "workspace. Starts bash with profile/rc isolation, sources "
                "~/.bashrc when present, then executes the command; default "
                "cwd is the workspace root and optional cwd must stay inside it."
            ),
            input_model=BashParams,
            tags={"builtin", "bash", "command"},
            dangerous=True,
        )

    async def create_coro(self, task: ToolCallTask, context: ToolContext) -> str:
        params = BashParams.model_validate(task.tool_call_params.params)
        return await self._runner.run(
            command=params.command,
            cwd=params.cwd,
            timeout_s=params.timeout_s,
            context=context,
        )

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        _ = task, reason


async def _read_stream(
    stream: asyncio.StreamReader | None,
    parts: list[str],
    entity_log: EntityLog | None,
) -> None:
    if stream is None:
        return
    while True:
        data = await stream.read(4096)
        if not data:
            return
        text = data.decode("utf-8", errors="replace")
        parts.append(text)
        if entity_log is not None:
            await entity_log.write(text)


def _render_terminal_result(
    *,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    timed_out: bool,
    duration_s: float,
    max_stderr_chars: int,
) -> str:
    rendered_stdout = _compact_stdout(stdout)
    rendered_stderr = _compact_stderr(stderr, max_stderr_chars)
    notes = [
        f"[exit_code={exit_code if exit_code is not None else 'unknown'}]",
        f"[timed_out={str(timed_out).lower()}]",
        f"[duration_s={duration_s:.3f}]",
    ]
    if rendered_stderr:
        notes.append("[stderr]")
        notes.append(rendered_stderr)
    if not rendered_stdout:
        notes.append("[stdout empty]")
        return "\n".join(notes)
    return rendered_stdout + "\n" + "\n".join(notes)


def _compact_stdout(stdout: str) -> str:
    if len(stdout) <= _MAX_INLINE_STDOUT_CHARS:
        return stdout
    capture_path = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}-stdout.log"
    capture_path.write_text(stdout, encoding="utf-8")
    return (
        stdout[:_STDOUT_HEAD_CHARS]
        + stdout[-_STDOUT_TAIL_CHARS:]
        + f"\n[stdout truncated: full output captured at {capture_path}]"
    )


def _compact_stderr(stderr: str, max_chars: int) -> str:
    if len(stderr) <= max_chars:
        return stderr
    return (
        stderr[: max_chars // 2]
        + "\n[stderr truncated]\n"
        + stderr[-(max_chars - max_chars // 2) :]
    )


register_tool_type("bash", BashTool)
