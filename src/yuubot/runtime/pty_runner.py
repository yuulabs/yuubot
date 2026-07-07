"""Shared PTY process runner for runtime tasks and admin terminal."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ptyprocess import PtyProcessUnicode  # type: ignore[import-untyped]

from .streams import TextStream


async def run_pty_process(
    *,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    stdin_stream: TextStream,
    stdout_stream: TextStream,
    rows: int = 24,
    cols: int = 80,
) -> int:
    process = PtyProcessUnicode.spawn(
        argv,
        cwd=str(cwd),
        env=env,
        dimensions=(rows, cols),
    )
    read_task = asyncio.create_task(_read_loop(process, stdout_stream), name="pty_read")
    stdin_task = asyncio.create_task(_stdin_loop(process, stdin_stream), name="pty_stdin")
    try:
        exit_status = await asyncio.to_thread(process.wait)
        return int(exit_status) if exit_status is not None else 0
    except asyncio.CancelledError:
        await terminate_pty(process)
        raise
    finally:
        read_task.cancel()
        stdin_task.cancel()
        await asyncio.gather(read_task, stdin_task, return_exceptions=True)


async def write_pty(process: PtyProcessUnicode, data: str) -> None:
    await asyncio.to_thread(process.write, data)


async def terminate_pty(process: PtyProcessUnicode) -> None:
    if not process.isalive():
        return
    await asyncio.to_thread(process.terminate, True)
    try:
        await asyncio.to_thread(process.wait)
    except Exception:
        pass


async def _read_loop(process: PtyProcessUnicode, stdout: TextStream) -> None:
    try:
        while process.isalive():
            data = await asyncio.to_thread(process.read, 4096)
            if not data:
                break
            stdout.write(data)
    except EOFError:
        pass
    except asyncio.CancelledError:
        raise


async def _stdin_loop(process: PtyProcessUnicode, stdin_stream: TextStream) -> None:
    try:
        async for chunk in stdin_stream.subscribe():
            await asyncio.to_thread(process.write, chunk)
    except asyncio.CancelledError:
        raise
