from __future__ import annotations

from pathlib import Path

import pytest

from support.api import base_url, boot_app, enable_integration, http_json, put_integration, running_server


@pytest.mark.asyncio
async def test_configure_integration_decodes_struct_body(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        snapshot = await put_integration(
            server,
            "github",
            name="GitHub",
            config={"access_token": "secret"},
        )
    assert snapshot["type"] == "github"
    assert snapshot["name"] == "GitHub"


@pytest.mark.asyncio
async def test_configure_integration_rejects_invalid_config_type(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        response = await http_json(
            "PUT",
            f"{base_url(server)}/api/integrations/github/config",
            {"name": "GitHub", "config": "not-an-object"},
            expected_status=400,
        )
    assert response["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_configure_tavily_web_uses_backend_defaults(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        snapshot = await put_integration(
            server,
            "tavily_web",
            name="web",
            config={"api_key": "secret"},
        )
        enabled = await enable_integration(server, "tavily_web")

    assert snapshot["config"] == {
        "api_key": "***",
        "tavily_base_url": "https://api.tavily.com",
        "timeout_s": 30.0,
        "user_agent": "yuubot/0.1",
        "max_read_bytes": 1048576,
        "max_read_chars": 12000,
        "max_download_bytes": 104857600,
    }
    assert enabled["enabled"] is True


@pytest.mark.asyncio
async def test_configure_tavily_web_rejects_string_integer(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        response = await http_json(
            "PUT",
            f"{base_url(server)}/api/integrations/tavily_web/config",
            {"name": "web", "config": {"api_key": "secret", "max_read_bytes": "1048576"}},
            expected_status=400,
        )

    assert response["error"]["code"] == "bad_request"
    assert "max_read_bytes" in str(response["error"])
