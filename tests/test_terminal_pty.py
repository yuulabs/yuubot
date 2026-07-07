from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import websockets

from support.api import base_url, boot_app, running_server
from yuubot.runtime.terminal import TerminalSession


@pytest.mark.asyncio
async def test_terminal_session_runs_command_with_real_pty(tmp_path: Path) -> None:
    frames: list[dict[str, object]] = []

    async def send(frame: dict[str, object]) -> None:
        frames.append(frame)

    session = TerminalSession(
        send=send,
        auth_user="admin",
        command="printf terminal-ok",
        cwd=str(tmp_path),
    )
    await session.start()
    for _ in range(50):
        if "terminal-ok" in str(frames):
            break
        await asyncio.sleep(0.02)
    await session.close()

    assert frames[0]["type"] == "terminal.opened"
    assert "terminal-ok" in str(frames)
    assert "terminal.closed" in [frame["type"] for frame in frames]


@pytest.mark.asyncio
async def test_terminal_websocket_opens_admin_pty(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        uri = f"{base_url(server).replace('http://', 'ws://')}/api/terminal/ws"
        async with websockets.connect(uri, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "type": "terminal.open",
                "payload": {"command": "printf ws-terminal-ok", "cwd": str(tmp_path)},
            }))
            seen = ""
            for _ in range(20):
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                seen += json.dumps(frame)
                if "ws-terminal-ok" in seen:
                    break
            await ws.send(json.dumps({"type": "terminal.close", "payload": {}}))

    assert "terminal.opened" in seen
    assert "ws-terminal-ok" in seen


@pytest.mark.asyncio
async def test_terminal_websocket_rejects_invalid_command_payload(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        uri = f"{base_url(server).replace('http://', 'ws://')}/api/terminal/ws"
        async with websockets.connect(uri, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "type": "terminal.open",
                "payload": {"command": "printf bad-input-test", "rows": "not-an-int"},
            }))
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert frame["type"] == "terminal.error"
            assert "rows" in frame["payload"]["message"].lower()
