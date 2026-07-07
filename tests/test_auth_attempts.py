from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from support.api import base_url, running_server
from yuubot import Yuubot
from yuubot.runtime.auth_attempts import AuthAttemptCreate


@pytest.mark.asyncio
async def test_auth_attempts_persist_across_app_reload(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    app = await Yuubot.create(data_dir)
    attempt = await app.create_auth_attempt(
        AuthAttemptCreate(
            connection_id="mcp:linear",
            method="oauth_pkce",
            action={"kind": "open_url", "url": "https://auth.example/authorize"},
            expires_at="2030-01-01T00:00:00Z",
        )
    )
    await app.update_auth_attempt(attempt.id, status="polling")
    await app.shutdown()

    reloaded = await Yuubot.create(data_dir)
    snapshots = reloaded.auth_attempt_snapshots()

    assert [item.id for item in snapshots] == [attempt.id]
    assert snapshots[0].connection_id == "mcp:linear"
    assert snapshots[0].status == "polling"
    assert snapshots[0].action == {"kind": "open_url", "url": "https://auth.example/authorize"}


@pytest.mark.asyncio
async def test_auth_attempt_admin_api_create_update_delete(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")

    async with running_server(app) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            created = await client.post(
                "/api/auth-attempts",
                json={
                    "connection_id": "mcp:github",
                    "method": "device_code",
                    "action": {"kind": "show_code", "code": "ABCD"},
                },
            )
            attempt_id = created.json()["id"]
            updated = await client.put(
                f"/api/auth-attempts/{attempt_id}",
                json={"status": "failed", "error": "expired", "action": {"kind": "retry"}},
            )
            listed = await client.get("/api/auth-attempts")
            deleted = await client.delete(f"/api/auth-attempts/{attempt_id}")
            missing = await client.delete(f"/api/auth-attempts/{attempt_id}")

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["status"] == "failed"
    assert updated.json()["error"] == "expired"
    assert listed.json()["items"][0]["id"] == attempt_id
    assert deleted.status_code == 200
    assert missing.status_code == 404
