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
        send,
        "admin",
        "printf terminal-ok",
        str(tmp_path),
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
async def test_terminal_websocket_protocol_and_admin_pty(tmp_path: Path) -> None:
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
        async with websockets.connect(uri, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "type": "terminal.open",
                "payload": {"command": "printf bad-input-test", "rows": "not-an-int"},
            }))
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert frame["type"] == "terminal.error"
            assert "rows" in frame["payload"]["message"].lower()
        async with websockets.connect(uri, open_timeout=5) as ws:
            await ws.send(json.dumps({
                "type": "terminal.open",
                "payload": {"command": "printf empty-input-test", "cwd": str(tmp_path)},
            }))
            opened = False
            for _ in range(20):
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                if frame["type"] == "terminal.opened":
                    opened = True
                    break
            assert opened
            await ws.send(json.dumps({"type": "terminal.input", "payload": {"data": ""}}))
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert frame["type"] == "terminal.error"
            assert "data" in frame["payload"]["message"].lower()

    assert "terminal.opened" in seen
    assert "ws-terminal-ok" in seen


@pytest.mark.asyncio
async def test_terminal_websocket_rejects_unauthenticated(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    from yuubot.app.deployment import DeploymentConfig
    from yuubot.web.auth import SessionStore
    from yuubot.web.routes.admin import create_admin_app

    app = await boot_app(tmp_path / "data")
    api = create_admin_app(app, DeploymentConfig(), SessionStore())
    with TestClient(api) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/api/terminal/ws"):
                pass
