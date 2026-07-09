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
async def test_configure_web_uses_backend_defaults(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        snapshot = await put_integration(
            server,
            "web",
            name="web",
            config={"tavily_api_key": "secret"},
        )
        enabled = await enable_integration(server, "web")

    assert snapshot["config"] == {
        "tavily_api_key": "***",
        "jina_api_key": "",
        "read_backends": ["jina", "tavily", "httpx"],
        "tavily_base_url": "https://api.tavily.com",
        "jina_base_url": "https://r.jina.ai",
        "timeout_s": 30.0,
        "jina_timeout_s": 30.0,
        "user_agent": "yuubot/0.1",
        "max_read_bytes": 1048576,
        "max_read_chars": 12000,
        "max_download_bytes": 104857600,
        "tavily_extract_depth": "basic",
        "tavily_extract_format": "markdown",
    }
    assert enabled["enabled"] is True


@pytest.mark.asyncio
async def test_configure_web_rejects_string_integer(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        response = await http_json(
            "PUT",
            f"{base_url(server)}/api/integrations/web/config",
            {"name": "web", "config": {"tavily_api_key": "secret", "max_read_bytes": "1048576"}},
            expected_status=400,
        )

    assert response["error"]["code"] == "bad_request"
    assert "max_read_bytes" in str(response["error"])
