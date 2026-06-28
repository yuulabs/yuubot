from __future__ import annotations

from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from yuubot.runtime.admin.handlers import (
    MaintenanceCommandResult,
    make_update_service_handler,
)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_update_handler_pulls_syncs_and_requests_restart(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    restarted = False

    async def runner(argv, cwd):
        calls.append(tuple(argv))
        assert cwd == tmp_path
        return MaintenanceCommandResult(tuple(argv), 0, "ok", "")

    def restart() -> None:
        nonlocal restarted
        restarted = True

    app = Starlette(
        routes=[
            Route(
                "/api/admin/update",
                make_update_service_handler(
                    repo_root=tmp_path,
                    command_runner=runner,
                    restart_requester=restart,
                ),
                methods=("POST",),
            )
        ]
    )

    async with _client(app) as client:
        response = await client.post("/api/admin/update")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert calls == [("git", "pull", "--ff-only"), ("uv", "sync")]
    assert restarted


async def test_update_handler_stops_before_restart_on_command_failure(
    tmp_path: Path,
) -> None:
    restarted = False

    async def runner(argv, cwd):
        return MaintenanceCommandResult(tuple(argv), 1, "", "nope")

    def restart() -> None:
        nonlocal restarted
        restarted = True

    app = Starlette(
        routes=[
            Route(
                "/api/admin/update",
                make_update_service_handler(
                    repo_root=tmp_path,
                    command_runner=runner,
                    restart_requester=restart,
                ),
                methods=("POST",),
            )
        ]
    )

    async with _client(app) as client:
        response = await client.post("/api/admin/update")

    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert not restarted


async def test_update_handler_requires_dev_supervisor_for_default_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("YUUBOT_DEV_SUPERVISOR_PID", raising=False)
    app = Starlette(
        routes=[
            Route(
                "/api/admin/update",
                make_update_service_handler(repo_root=tmp_path),
                methods=("POST",),
            )
        ]
    )

    async with _client(app) as client:
        response = await client.post("/api/admin/update")

    assert response.status_code == 409
    assert response.json()["detail"] == "service restart requires ybot dev"
