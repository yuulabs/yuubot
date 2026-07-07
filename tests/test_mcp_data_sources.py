from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from support.api import base_url, running_server
from yuubot import Yuubot
from yuubot.runtime.mcp import McpCapabilityIndex, McpOAuthTokenStorage, McpServerRecord, McpToolSpec, normalize_auth_mode, tool_signature


def test_normalize_auth_mode_maps_legacy_aliases() -> None:
    assert normalize_auth_mode("auto") == "oauth_auto"
    assert normalize_auth_mode("oauth") == "oauth_auto"
    assert normalize_auth_mode("oauth_manual") == "oauth_manual"


def test_mcp_tool_signature_compacts_json_schema() -> None:
    spec = tool_signature(
        McpToolSpec(
            name="search_issues",
            description="Search issues in Linear.",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                    "state": {"type": "string", "enum": ["open", "closed"]},
                    "filters": {"type": "object"},
                },
            },
        )
    )

    assert "search_issues(query: str, limit: int = 20" in spec
    assert "state: Literal['open', 'closed'] | None = None" in spec
    assert "filters: dict | None = None" in spec
    assert "Search issues in Linear." in spec


@pytest.mark.asyncio
async def test_mcp_server_lifecycle_keeps_secret_out_of_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await Yuubot.create(tmp_path / "data")
    record = McpServerRecord(
        id="linear",
        name="Linear",
        endpoint_url="https://mcp.example.invalid",
        auth_mode="api_key",
    )

    await app.configure_mcp_server(record, api_key="secret-token")

    async def fake_discover(_manager: object, server: McpServerRecord) -> McpCapabilityIndex:
        assert server.id == "linear"
        return McpCapabilityIndex(
            server_id="linear",
            tools=(McpToolSpec(name="search_issues", description="Search issues", input_schema={"type": "object"}),),
        )

    monkeypatch.setattr(type(app.runtime.mcps), "discover", fake_discover)
    state = await app.enable_mcp_server("linear")
    snapshots = await app.mcp_server_snapshots()

    assert state.status == "ready"
    assert snapshots[0]["credential_configured"] is True
    assert "secret-token" not in str(snapshots)
    assert app.runtime.mcps.search("issues")[0].name == "search_issues"
    assert await app.runtime.credentials.secret_payload("mcp:linear:api_key") == {
        "api_key": "secret-token",
        "header": "Authorization",
        "prefix": "Bearer ",
    }


@pytest.mark.asyncio
async def test_credentials_admin_api_lists_redacted_records_and_revokes(tmp_path: Path) -> None:
    import httpx

    app = await Yuubot.create(tmp_path / "data")
    await app.configure_mcp_server(
        McpServerRecord(
            id="linear",
            name="Linear",
            endpoint_url="https://mcp.example.invalid",
            auth_mode="api_key",
        ),
        api_key="secret-token",
    )

    async with running_server(app) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            listed = await client.get("/api/credentials")
            deleted = await client.delete("/api/credentials/mcp:linear:api_key")
            servers = await client.get("/api/mcp-servers")

    payload = listed.json()
    assert listed.status_code == 200
    assert deleted.status_code == 200
    assert payload["items"][0]["id"] == "mcp:linear:api_key"
    assert payload["items"][0]["redacted_summary"] == "configured"
    assert "secret-token" not in str(payload)
    assert servers.json()["items"][0]["credential_configured"] is False


@pytest.mark.asyncio
async def test_yb_mcps_facade_uses_summary_search_and_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yb.mcps

    app = await Yuubot.create(tmp_path / "data")
    record = McpServerRecord(
        id="linear",
        name="Linear",
        endpoint_url="https://mcp.example.invalid",
        enabled=True,
    )
    index = McpCapabilityIndex(
        server_id="linear",
        tools=(
            McpToolSpec(
                name="search_issues",
                description="Search issues",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                },
            ),
        ),
    )
    app.runtime.mcps.records[record.id] = record
    app.runtime.mcps.bind([record], [index])
    async with running_server(app) as server:
        monkeypatch.setenv("YUUBOT_DAEMON_URL", base_url(server))

        matches = await yb.mcps.search("issues")
        client = yb.mcps.get_client("linear")
        spec = await client.get_spec("search_issues")

    assert [(item.server_id, item.name) for item in matches] == [("linear", "search_issues")]
    assert "query: str" in spec


@pytest.mark.asyncio
async def test_mcp_oauth_storage_persists_sdk_models_in_credential_store(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    record = McpServerRecord(
        id="oauth",
        name="OAuth MCP",
        endpoint_url="https://mcp.example.invalid",
        auth_mode="oauth_auto",
        credential_id="mcp:oauth:oauth",
    )
    storage = McpOAuthTokenStorage(app.runtime.credentials, record)

    await storage.set_client_info(
        OAuthClientInformationFull.model_validate({
            "redirect_uris": ["http://127.0.0.1/callback"],
            "client_id": "client-1",
            "client_secret": "client-secret",
        })
    )
    await storage.set_tokens(OAuthToken(access_token="access-token", refresh_token="refresh-token", scope="issues read"))

    credential = await app.runtime.credentials.get("mcp:oauth:oauth")
    secret = await app.runtime.credentials.secret_payload("mcp:oauth:oauth")

    assert credential is not None
    assert credential.kind == "oauth_token"
    assert credential.scopes == ("issues", "read")
    assert "access-token" not in str(credential)
    client_info = await storage.get_client_info()
    tokens = await storage.get_tokens()
    assert client_info is not None
    assert tokens is not None
    assert client_info.client_id == "client-1"
    assert tokens.access_token == "access-token"
    assert secret is not None
    assert secret["tokens"] != {}


@pytest.mark.asyncio
async def test_mcp_oauth_manual_config_keeps_client_secret_encrypted(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    record = await app.configure_mcp_server(
        McpServerRecord(
            id="manual",
            name="Manual OAuth MCP",
            endpoint_url="https://mcp.example.invalid",
            auth_mode="oauth_manual",
            oauth_issuer="https://auth.example",
            oauth_authorization_endpoint="https://auth.example/authorize",
            oauth_token_endpoint="https://auth.example/token",
            oauth_client_id="client-1",
            oauth_scope="issues read",
        ),
        oauth_client_secret="client-secret",
    )
    storage = McpOAuthTokenStorage(app.runtime.credentials, record, redirect_uri="http://127.0.0.1/callback")
    client_info = await storage.get_client_info()
    snapshots = await app.mcp_server_snapshots()
    secret = await app.runtime.credentials.secret_payload("mcp:manual:oauth")

    assert client_info is not None
    assert client_info.client_id == "client-1"
    assert client_info.client_secret == "client-secret"
    assert snapshots[0]["oauth_client_id"] == "client-1"
    assert "client-secret" not in str(snapshots)
    assert secret is not None
    assert secret["manual_client_secret"] == "client-secret"


@pytest.mark.asyncio
async def test_mcp_oauth_admin_start_and_callback_complete_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    app = await Yuubot.create(tmp_path / "data")
    await app.configure_mcp_server(
        McpServerRecord(
            id="oauth",
            name="OAuth MCP",
            endpoint_url="https://mcp.example.invalid",
            auth_mode="oauth_auto",
        )
    )

    async def fake_discover_with_oauth(
        _manager: object,
        record: McpServerRecord,
        *,
        redirect_uri: str,
        redirect_handler: Callable[[str], Awaitable[None]],
        callback_handler: Callable[[], Awaitable[tuple[str, str | None]]],
        timeout_s: float,
    ) -> McpCapabilityIndex:
        assert record.credential_id == "mcp:oauth:oauth"
        assert redirect_uri.endswith("/callback")
        assert timeout_s == 600
        await redirect_handler("https://auth.example/authorize?state=state-1")
        code, state = await callback_handler()
        assert (code, state) == ("code-1", "state-1")
        return McpCapabilityIndex(
            server_id="oauth",
            tools=(McpToolSpec(name="search", description="Search", input_schema={"type": "object"}),),
        )

    monkeypatch.setattr(type(app.runtime.mcps), "discover_with_oauth", fake_discover_with_oauth)

    async with running_server(app) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            start = await client.post("/api/mcp-servers/oauth/auth/start")
            attempt = start.json()
            callback = await client.get(f"/api/mcp-oauth/{attempt['id']}/callback?code=code-1&state=state-1")
            for _ in range(50):
                attempts = (await client.get("/api/auth-attempts")).json()["items"]
                current = next(item for item in attempts if item["id"] == attempt["id"])
                if current["status"] == "succeeded":
                    break
            servers = (await client.get("/api/mcp-servers")).json()["items"]

    assert start.status_code == 202
    assert attempt["status"] == "waiting_for_user"
    assert attempt["action"]["url"] == "https://auth.example/authorize?state=state-1"
    assert callback.status_code == 200
    assert current["status"] == "succeeded"
    assert servers[0]["status"] == "ready"
    assert servers[0]["tools_count"] == 1
