from __future__ import annotations

from pathlib import Path

import pytest

from support.api import base_url, boot_app, http_json, put_integration, running_server


@pytest.mark.asyncio
async def test_configure_integration_decodes_struct_body(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        snapshot = await put_integration(
            server,
            "github",
            name="GitHub",
            config={"token": "secret"},
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
