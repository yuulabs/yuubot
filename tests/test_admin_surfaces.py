from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest

from support.api import base_url
from yuubot import Yuubot
from yuubot.app.deployment import AdminAuthConfig, AdminAuthBuiltinConfig, AdminAuthProxyConfig, DeploymentConfig
from yuubot.web.auth import SessionStore
from yuubot.web.server import UvicornServer, make_server


@contextlib.asynccontextmanager
async def surface_server(app: Yuubot, deployment: DeploymentConfig) -> AsyncIterator[UvicornServer]:
    server = make_server(app, port=0, deployment=deployment)
    ready = asyncio.Event()
    serve_task = asyncio.create_task(server.serve())

    async def wait_ready() -> None:
        while not server._server.started:  # noqa: SLF001
            await asyncio.sleep(0)
        ready.set()

    wait_task = asyncio.create_task(wait_ready())
    try:
        await asyncio.wait_for(ready.wait(), timeout=10.0)
        yield server
    finally:
        wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await wait_task
        server.shutdown()
        await serve_task


@pytest.mark.asyncio
async def test_public_surface_exposes_public_routes_only(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(surface="public")

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            api = await client.get("/api/bootstrap")
            spa = await client.get("/")
            missing_share = await client.get("/s/missing")

    assert api.status_code == 404
    assert spa.status_code == 404
    assert missing_share.status_code == 404


@pytest.mark.asyncio
async def test_local_admin_surface_does_not_require_login(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(surface="local_admin")

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            response = await client.get("/api/bootstrap")

    assert response.status_code == 200
    assert response.json()["auth"]["mode"] == "none"


@pytest.mark.asyncio
async def test_trusted_builtin_requires_login_even_from_loopback(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(
        surface="trusted_admin",
        admin_auth=AdminAuthConfig(mode="builtin", builtin=AdminAuthBuiltinConfig(password="secret")),
    )

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            unauthenticated = await client.get("/api/bootstrap")
            html_redirect = await client.get("/admin/conversations", headers={"accept": "text/html"}, follow_redirects=False)
            login = await client.post("/api/auth/login", json={"password": "secret"})
            csrf_token = cast(str, login.json()["csrf_token"])
            authenticated = await client.get("/api/bootstrap")
            missing_csrf = await client.post("/api/auth/logout")
            logged_out = await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})

    assert unauthenticated.status_code == 401
    assert html_redirect.status_code == 303
    assert html_redirect.headers["location"].startswith("/login?redirect=")
    assert login.status_code == 200
    assert authenticated.status_code == 200
    assert authenticated.json()["auth"]["mode"] == "builtin"
    assert missing_csrf.status_code == 403
    assert logged_out.status_code == 200


@pytest.mark.asyncio
async def test_trusted_proxy_requires_proxy_user_header(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(
        surface="trusted_admin",
        admin_auth=AdminAuthConfig(mode="proxy", proxy=AdminAuthProxyConfig(user_header="X-Forwarded-User")),
    )

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            rejected = await client.get("/api/bootstrap")
            accepted = await client.get("/api/bootstrap", headers={"X-Forwarded-User": "alice"})

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["auth"]["method"] == "proxy"


def test_builtin_session_expires_after_seven_idle_days() -> None:
    now = 1000.0
    sessions = SessionStore(now=lambda: now)
    session_id, _csrf = sessions.create(user_id="admin", display_name="Admin")

    assert sessions.get(session_id) is not None
    now += (7 * 24 * 60 * 60) - 1
    sessions.touch(session_id)
    assert sessions.get(session_id) is not None
    now += (7 * 24 * 60 * 60) + 1
    assert sessions.get(session_id) is None
