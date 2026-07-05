import asyncio
import os
from pathlib import Path
from typing import Final, cast

import msgspec

from .base import workspace_tool

TAIL_LINES: Final[int] = 50
DEFAULT_TIMEOUT_S: Final[float] = 30

DESCRIPTION = """Run a bash command with the actor workspace as the working directory.

The command is executed as `bash -lc <command>`, so shell features such as pipes, redirects, and environment expansion are available. The process inherits the daemon environment, including PATH and tooling such as uv or pnpm when configured on the host.

`timeout_s` defaults to 30 seconds. When a timeout occurs, the process is killed and the result marks `timeout: true`.

The result includes `exit_code`, `timeout`, `stdout`, and `stderr`. Long stdout/stderr output is truncated to the last 50 lines, with a note showing how many lines were omitted.

Use this for shell-native workspace operations such as package installation, git commands, or running CLI tools. Prefer `execute_python` for orchestration, data shaping, and integration facade calls. Avoid printing huge outputs; filter to the relevant data before returning."""


class BashPayload(msgspec.Struct, frozen=True, kw_only=True):
    command: str
    timeout_s: float | None = None


def _tail(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= TAIL_LINES:
        return text.rstrip()
    return "\n".join([f"[truncated: showing last {TAIL_LINES} of {len(lines)} lines)", *lines[-TAIL_LINES:]])


async def _execute_bash(root: Path, payload: msgspec.Struct) -> str:
    data = cast(BashPayload, payload)
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        data.command,
        cwd=root,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(proc.communicate(), timeout=data.timeout_s or DEFAULT_TIMEOUT_S)
        timeout = False
        code = proc.returncode or 0
    except TimeoutError:
        proc.kill()
        raw_stdout, raw_stderr = await proc.communicate()
        timeout = True
        code = -1
    return "\n".join(
        [
            f"exit_code: {code}",
            f"timeout: {timeout}",
            "stdout:",
            _tail(raw_stdout.decode("utf-8", errors="replace")),
            "stderr:",
            _tail(raw_stderr.decode("utf-8", errors="replace")),
        ]
    )


BASH_SPEC = workspace_tool(payload_type=BashPayload, description=DESCRIPTION, execute=_execute_bash)
