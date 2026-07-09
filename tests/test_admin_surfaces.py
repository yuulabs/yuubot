from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import msgspec
import httpx
import pytest

from support.api import base_url
from yuubot import Yuubot
from yuubot.app.deployment import (
    DEFAULT_HOST,
    AdminAuthConfig,
    AdminAuthBuiltinConfig,
    AdminAuthProxyConfig,
    DeploymentConfig,
    deployment_listeners_for_serve,
)
from yuubot.domain.records import ActorInput, ActorModelInput, RouteRecord
from yuubot.integrations.records import IntegrationRecord
from yuubot.llm import ModelCardInput, ProviderInput, ScriptedProvider
from yuubot.domain.stream import StreamEvent, StreamStopPayload
from yuubot.runtime.mcp import McpCapabilityIndex, McpServerRecord, McpToolSpec
from yuubot.web.auth import SessionStore, is_auth_exempt
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
            oauth_callback = await client.get("/api/mcp-oauth/missing/callback?code=code-1")
            spa = await client.get("/")
            missing_share = await client.get("/s/missing")

    assert api.status_code == 404
    assert oauth_callback.status_code == 404
    assert "Authorization attempt not found" in oauth_callback.text
    assert spa.status_code == 404
    assert missing_share.status_code == 404


@pytest.mark.asyncio
async def test_local_admin_surface_does_not_require_login(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(surface="local_admin", public_url_base="https://public.example.com")

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            response = await client.get("/api/bootstrap")

    assert response.status_code == 200
    assert response.json()["auth"]["mode"] == "none"
    assert response.json()["public_url_base"] == "https://public.example.com"


@pytest.mark.asyncio
async def test_trusted_builtin_requires_login_even_from_loopback(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(
        surface="trusted_admin",
        admin_auth=AdminAuthConfig("builtin", AdminAuthBuiltinConfig(username="alice", password="secret")),
    )

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            unauthenticated = await client.get("/api/bootstrap")
            html_redirect = await client.get("/admin/conversations", headers={"accept": "text/html"}, follow_redirects=False)
            bad_user = await client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
            login = await client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
            csrf_token = cast(str, login.json()["csrf_token"])
            authenticated = await client.get("/api/bootstrap")
            missing_csrf = await client.post("/api/auth/logout")
            logged_out = await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})

    assert unauthenticated.status_code == 401
    assert html_redirect.status_code == 303
    assert html_redirect.headers["location"].startswith("/login?redirect=")
    assert bad_user.status_code == 401
    assert login.status_code == 200
    assert authenticated.status_code == 200
    assert authenticated.json()["auth"]["mode"] == "builtin"
    assert missing_csrf.status_code == 403
    assert logged_out.status_code == 200


def test_builtin_auth_exempts_login_shell_assets_only() -> None:
    assert is_auth_exempt({"type": "http", "method": "GET", "path": "/login"})
    assert is_auth_exempt({"type": "http", "method": "HEAD", "path": "/login"})
    assert is_auth_exempt({"type": "http", "method": "GET", "path": "/assets/index.js"})
    assert is_auth_exempt({"type": "http", "method": "HEAD", "path": "/assets/index.css"})
    assert is_auth_exempt({"type": "http", "method": "GET", "path": "/sw.js"})
    assert is_auth_exempt({"type": "http", "method": "POST", "path": "/api/auth/login"})

    assert not is_auth_exempt({"type": "http", "method": "GET", "path": "/"})
    assert not is_auth_exempt({"type": "http", "method": "GET", "path": "/admin/conversations"})
    assert not is_auth_exempt({"type": "http", "method": "GET", "path": "/api/bootstrap"})
    assert not is_auth_exempt({"type": "http", "method": "POST", "path": "/api/auth/logout"})
    assert not is_auth_exempt({"type": "websocket", "path": "/ws"})


@pytest.mark.asyncio
async def test_trusted_admin_exposes_mcp_oauth_callback_without_admin_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await Yuubot.create(tmp_path / "data")
    await app.configure_mcp_server(
        McpServerRecord(
            "oauth",
            "OAuth MCP",
            "https://mcp.example.invalid",
            auth_mode="oauth_auto",
        )
    )

    async def fake_discover_with_oauth(
        _manager: object,
        record: McpServerRecord,
        redirect_uri: str,
        redirect_handler: Callable[[str], Awaitable[None]],
        callback_handler: Callable[[], Awaitable[tuple[str, str | None]]],
        timeout_s: float,
    ) -> McpCapabilityIndex:
        assert record.credential_id == "mcp:oauth:oauth"
        assert "/api/mcp-oauth/" in redirect_uri
        assert "token=" in redirect_uri
        assert timeout_s == 600
        await redirect_handler("https://auth.example/authorize?state=state-1")
        code, state = await callback_handler()
        assert (code, state) == ("code-1", "state-1")
        return McpCapabilityIndex(
            "oauth",
            (McpToolSpec("search", "Search", {"type": "object"}),),
        )

    monkeypatch.setattr(type(app.runtime.mcps), "discover_with_oauth", fake_discover_with_oauth)
    deployment = DeploymentConfig(
        surface="trusted_admin",
        admin_auth=AdminAuthConfig("builtin", AdminAuthBuiltinConfig(password="secret")),
    )

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as admin_client:
            login = await admin_client.post("/api/auth/login", json={"username": "admin", "password": "secret"})
            csrf_token = cast(str, login.json()["csrf_token"])
            start = await admin_client.post("/api/mcp-servers/oauth/auth/start", headers={"X-CSRF-Token": csrf_token})
            attempt = start.json()
            parsed = urlparse(cast(str, attempt["action"]["callback_url"]))

            async with httpx.AsyncClient(base_url=base_url(server)) as callback_client:
                callback = await callback_client.get(f"{parsed.path}?{parsed.query}&code=code-1&state=state-1")

            for _ in range(50):
                attempts = (await admin_client.get("/api/auth-attempts")).json()["items"]
                current = next(item for item in attempts if item["id"] == attempt["id"])
                if current["status"] == "succeeded":
                    break

    assert start.status_code == 202
    assert callback.status_code == 200
    assert current["status"] == "succeeded"


@pytest.mark.asyncio
async def test_trusted_proxy_requires_proxy_user_header(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    deployment = DeploymentConfig(
        surface="trusted_admin",
        admin_auth=AdminAuthConfig("proxy", proxy=AdminAuthProxyConfig("X-Forwarded-User")),
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


def test_builtin_session_store_prunes_expired_sessions_without_lookup() -> None:
    now = 1000.0
    sessions = SessionStore(now=lambda: now)
    old_session_id, _old_csrf = sessions.create(user_id="old", display_name=None)
    now += (7 * 24 * 60 * 60) + 1
    active_session_id, _active_csrf = sessions.create(user_id="active", display_name=None)

    assert sessions.prune_expired() == 1
    assert sessions.get(old_session_id) is None
    assert sessions.get(active_session_id) is not None


def test_local_admin_listener_host_is_code_enforced_loopback() -> None:
    deployments = deployment_listeners_for_serve(
        {
            "local_admin_server": {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 9001,
                "url_base": "https://admin.example.com",
            },
            "trusted_admin_server": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 9002,
                "auth": {"mode": "proxy"},
            },
        },
        "0.0.0.0",
        8765,
    )

    local = next(deployment for deployment in deployments if deployment.surface == "local_admin")
    trusted = next(deployment for deployment in deployments if deployment.surface == "trusted_admin")

    assert local.server.host == DEFAULT_HOST
    assert local.local_admin_server.host == DEFAULT_HOST
    assert local.server.port == 9001
    assert local.admin_url_base == "http://127.0.0.1:9001"
    assert trusted.server.host == "127.0.0.1"
    assert trusted.server.port == 9002


def test_trusted_builtin_rejects_empty_password_config() -> None:
    with pytest.raises(ValueError, match="trusted_admin_server.auth.builtin.password must be set"):
        deployment_listeners_for_serve(
            {
                "local_admin_server": {"enabled": False},
                "trusted_admin_server": {
                    "enabled": True,
                    "auth": {"mode": "builtin", "builtin": {"password": ""}},
                },
            },
            "127.0.0.1",
            8765,
        )


def test_trusted_builtin_rejects_empty_username_config() -> None:
    with pytest.raises(ValueError, match="trusted_admin_server.auth.builtin.username must be set"):
        deployment_listeners_for_serve(
            {
                "local_admin_server": {"enabled": False},
                "trusted_admin_server": {
                    "enabled": True,
                    "auth": {"mode": "builtin", "builtin": {"username": "", "password": "secret"}},
                },
            },
            "127.0.0.1",
            8765,
        )


@pytest.mark.asyncio
async def test_public_surface_webhook_requires_hmac_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "webhook-secret"
    monkeypatch.setenv("YUUBOT_GITHUB_WEBHOOK_SECRET", secret)
    app = await Yuubot.create(tmp_path / "data")
    app.provider_instances["fake"] = ScriptedProvider([[StreamEvent("stop", "stream_stop", StreamStopPayload("stop"))]])
    await app.put_provider(
        "fake",
        ProviderInput(
            "Fake",
            "openai-compatible",
            {"endpoint": "", "api_key": "test-key", "options": {}},
        ),
    )
    await app.put_model_card(
        "fake",
        "fake",
        ModelCardInput(toolcall=True, input_price_per_million=1.0, output_price_per_million=1.0),
    )
    await app.put_actor(
        "amy",
        ActorInput(name="Amy", workspace=str(tmp_path / "workspace"), provider="fake", model=ActorModelInput("fake")),
    )
    await app.enable_actor("amy")
    integration = IntegrationRecord("github", "github", "gh", {"access_token": "test-token"})
    await app.configure_integration(integration)
    await app.enable_integration(integration)
    await app.put_route(RouteRecord(id="mailbox", integration_type="github", pattern="mailbox", actor_id="amy"))

    deployment = DeploymentConfig(surface="public")
    body = msgspec.json.encode({"route": "mailbox", "text": "hello"})
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    async with surface_server(app, deployment) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            unsigned = await client.post("/webhooks/app/github", content=body, headers={"content-type": "application/json"})
            signed = await client.post(
                "/webhooks/app/github",
                content=body,
                headers={"content-type": "application/json", "x-yuubot-webhook-signature": f"sha256={signature}"},
            )

    assert unsigned.status_code == 401
    assert signed.status_code == 200
    assert signed.json()["delivered"] is True
