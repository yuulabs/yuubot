"""PTY WebSocket terminal handler — stdlib only, no extra deps."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import struct
import subprocess
import termios

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def _preexec() -> None:
    os.setsid()
    # FD 0 is already the slave PTY after subprocess's dup2; TIOCSCTTY makes it
    # the controlling terminal for the new session created by setsid().
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass


async def handle_terminal(websocket: WebSocket) -> None:
    await websocket.accept()

    master_fd, slave_fd = pty.openpty()
    _set_winsize(master_fd, 24, 80)

    env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-l"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=_preexec,
            env=env,
        )
    except Exception as exc:
        logger.error("Failed to spawn terminal shell: {}", exc)
        os.close(master_fd)
        os.close(slave_fd)
        await websocket.close()
        return

    os.close(slave_fd)

    loop = asyncio.get_running_loop()
    read_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _on_readable() -> None:
        try:
            data = os.read(master_fd, 4096)
            read_q.put_nowait(data)
        except OSError:
            read_q.put_nowait(None)
            loop.remove_reader(master_fd)

    loop.add_reader(master_fd, _on_readable)

    async def _pty_to_ws() -> None:
        while True:
            data = await read_q.get()
            if data is None:
                break
            try:
                await websocket.send_bytes(data)
            except Exception:
                break

    async def _ws_to_pty() -> None:
        while True:
            try:
                msg = await websocket.receive()
            except (WebSocketDisconnect, Exception):
                break
            if msg.get("type") == "websocket.disconnect":
                break
            if raw := msg.get("bytes"):
                try:
                    os.write(master_fd, raw)
                except OSError:
                    break
            elif text := msg.get("text"):
                try:
                    ev = json.loads(text)
                    if ev.get("type") == "resize":
                        _set_winsize(
                            master_fd,
                            int(ev.get("rows", 24)),
                            int(ev.get("cols", 80)),
                        )
                except Exception:
                    pass

    t1 = asyncio.create_task(_pty_to_ws())
    t2 = asyncio.create_task(_ws_to_pty())
    await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for t in (t1, t2):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    loop.remove_reader(master_fd)
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass
    try:
        os.close(master_fd)
    except OSError:
        pass
    logger.debug("Terminal session closed")
