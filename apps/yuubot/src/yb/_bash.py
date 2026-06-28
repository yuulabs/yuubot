"""Local bash helper for the handwritten yb facade."""

from __future__ import annotations

import asyncio
import locale
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


async def bash(
    command: str,
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    check: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[list[str]]:
    """Run a bash command locally with the user's bashrc loaded."""
    argv = ["bash", "-ic", command]
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=os.fspath(cwd) if cwd is not None else None,
        env=process_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        process.kill()
        stdout, stderr = await process.communicate()
        raise subprocess.TimeoutExpired(
            argv,
            timeout,
            output=_decode(stdout),
            stderr=_clean_bash_stderr(_decode(stderr)),
        ) from exc

    result = subprocess.CompletedProcess(
        argv,
        process.returncode,
        _decode(stdout),
        _clean_bash_stderr(_decode(stderr)),
    )
    if check:
        result.check_returncode()
    return result


def _decode(output: bytes | None) -> str:
    if output is None:
        return ""
    return output.decode(locale.getpreferredencoding(False), errors="replace")


def _clean_bash_stderr(stderr: str) -> str:
    noise = (
        "bash: cannot set terminal process group",
        "bash: no job control in this shell",
    )
    lines = [line for line in stderr.splitlines() if not line.startswith(noise)]
    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if stderr.endswith("\n") else "")
