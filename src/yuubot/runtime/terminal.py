"""Admin-owned server-side PTY sessions."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

from attrs import define, field
from ptyprocess import PtyProcessUnicode  # type: ignore[import-untyped]

from .pty_runner import terminate_pty, write_pty

TerminalSend = Callable[[dict[str, object]], Awaitable[None]]


@define
class TerminalSession:
    send: TerminalSend
    auth_user: str
    command: str = ""
    cwd: str = "~"
    rows: int = 24
    cols: int = 80
    _process: PtyProcessUnicode | None = field(default=None, init=False)
    _pump: asyncio.Task[None] | None = field(default=None, init=False)

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("terminal session is already open")
        argv = _argv(self.command)
        self._process = PtyProcessUnicode.spawn(
            argv,
            cwd=str(Path(self.cwd).expanduser()),
            env=dict(os.environ),
            dimensions=(self.rows, self.cols),
        )
        await self.send({
            "type": "terminal.opened",
            "payload": {
                "pid": self._process.pid,
                "argv": argv,
                "cwd": str(Path(self.cwd).expanduser()),
                "auth_user": self.auth_user,
            },
        })
        self._pump = asyncio.create_task(self._read_loop(), name="terminal_read")

    async def write(self, data: str) -> None:
        await write_pty(self._require_process(), data)

    async def resize(self, *, rows: int, cols: int) -> None:
        process = self._require_process()
        safe_rows = max(1, min(rows, 200))
        safe_cols = max(1, min(cols, 400))
        await asyncio.to_thread(process.setwinsize, safe_rows, safe_cols)

    async def close(self) -> None:
        pump = self._pump
        self._pump = None
        if pump is not None:
            pump.cancel()
        process = self._process
        self._process = None
        exit_status: int | None = None
        if process is not None:
            await terminate_pty(process)
            try:
                exit_status = await asyncio.to_thread(process.wait)
            except Exception:
                exit_status = None
        if pump is not None:
            try:
                await pump
            except asyncio.CancelledError:
                pass
        await self.send({"type": "terminal.closed", "payload": {"exit_status": exit_status}})

    async def _read_loop(self) -> None:
        process = self._require_process()
        try:
            while process.isalive():
                data = await asyncio.to_thread(process.read, 4096)
                if not data:
                    break
                await self.send({"type": "terminal.output", "payload": {"data": data}})
        except EOFError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.send({"type": "terminal.error", "payload": {"message": str(exc)}})
        finally:
            current = self._process
            if current is not None and current is process and not process.isalive():
                try:
                    exit_status = await asyncio.to_thread(process.wait)
                except Exception:
                    exit_status = None
                await self.send({"type": "terminal.exited", "payload": {"exit_status": exit_status}})

    def _require_process(self) -> PtyProcessUnicode:
        if self._process is None:
            raise RuntimeError("terminal session is not open")
        return self._process


def _argv(command: str) -> list[str]:
    shell = os.environ.get("SHELL") or "/bin/sh"
    if command.strip():
        return [shell, "-lc", command]
    return shlex.split(shell) or ["/bin/sh"]
