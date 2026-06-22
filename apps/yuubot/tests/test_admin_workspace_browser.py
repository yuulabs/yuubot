"""Admin-served actor-workspace browser tests.

Covers the backend HTTP surface:
  * GET /workspace/{actor_id}/         — directory listing (http.server-style)
  * GET /workspace/{actor_id}/{path}   — nested dir listing / file response
  * GET /workspace/{actor_id}/<..>      — 403 on path escape
  * GET /workspace/<unknown>/          — 404 on nonexistent actor workspace
"""

from __future__ import annotations

import msgspec
from pathlib import Path

import httpx
import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.actors.workspace import safe_actor_path_id
from yuubot.core.integrations import default_integration_factories
from yuubot.resources.root import Resources
from yuubot.runtime.admin import DaemonClient, build_admin_asgi_app
from yuubot.runtime.plugin import ExternalPluginManager

ACTOR_ID = "actor-1"


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = (
        tmp_path
        / "workspace"
        / "actors"
        / safe_actor_path_id(ACTOR_ID)
    )
    root.mkdir(parents=True)
    return root


@pytest.fixture
def admin_app(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
):
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        "<main>yuubot monitor</main>", encoding="utf-8"
    )
    return build_admin_asgi_app(
        config=msgspec.structs.replace(
            yuubot_config.admin,
            web_dist_dir=str(web_dist),
        ),
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
        plugin_manager=ExternalPluginManager(
            plugins_dir=tmp_path / "plugins",
            data_root=tmp_path,
        ),
    )


async def test_workspace_index_lists_files_and_dirs_and_hidden(
    admin_app,
    workspace_root: Path,
) -> None:
    (workspace_root / "outputs").mkdir()
    (workspace_root / "outputs" / "chart.html").write_text(
        "<h1>chart</h1>", encoding="utf-8"
    )
    (workspace_root / ".secret").write_text("secret", encoding="utf-8")

    async with _client(admin_app) as client:
        response = await client.get(f"/workspace/{ACTOR_ID}/")

    assert response.status_code == 200
    body = response.text
    assert "outputs/" in body
    assert ".secret" in body
    assert "chart.html" not in body  # nested file is not listed at root index


async def test_workspace_nested_file_response_has_mime(
    admin_app,
    workspace_root: Path,
) -> None:
    (workspace_root / "outputs").mkdir()
    (workspace_root / "outputs" / "chart.html").write_text(
        "<h1>chart</h1>", encoding="utf-8"
    )
    (workspace_root / "outputs" / "pic.png").write_bytes(b"\x89PNG\r\n")

    async with _client(admin_app) as client:
        html = await client.get(
            f"/workspace/{ACTOR_ID}/outputs/chart.html"
        )
        png = await client.get(
            f"/workspace/{ACTOR_ID}/outputs/pic.png"
        )

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert html.text == "<h1>chart</h1>"

    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"


async def test_workspace_nested_directory_listing_has_parent_link(
    admin_app,
    workspace_root: Path,
) -> None:
    (workspace_root / "outputs").mkdir()
    (workspace_root / "outputs" / "chart.html").write_text(
        "x", encoding="utf-8"
    )

    async with _client(admin_app) as client:
        response = await client.get(
            f"/workspace/{ACTOR_ID}/outputs/"
        )

    assert response.status_code == 200
    body = response.text
    assert "chart.html" in body
    assert "../" in body  # parent link


async def test_workspace_path_escape_returns_403(admin_app) -> None:
    # Percent-encoded traversal so httpx does not normalise the .. segments
    # before the route matches.
    escapes = "..%2f..%2f..%2f..%2fetc%2fpasswd"
    async with _client(admin_app) as client:
        response = await client.get(
            f"/workspace/{ACTOR_ID}/{escapes}"
        )

    assert response.status_code == 403


async def test_workspace_unknown_actor_returns_404(
    admin_app,
    tmp_path: Path,
) -> None:
    assert not (tmp_path / "workspace" / "actors" /
                safe_actor_path_id("nope")).exists()

    async with _client(admin_app) as client:
        response = await client.get("/workspace/nope/")

    assert response.status_code == 404


async def test_workspace_missing_file_returns_404(
    admin_app,
    workspace_root: Path,
) -> None:
    async with _client(admin_app) as client:
        response = await client.get(
            f"/workspace/{ACTOR_ID}/missing.html"
        )

    assert response.status_code == 404
