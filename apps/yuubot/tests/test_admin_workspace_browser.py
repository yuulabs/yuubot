"""Admin workspace browser tests — direct path-based serving.

Covers the backend HTTP surface after dropping the ``safe_actor_path_id``
slug indirection. The user-configured ``CapabilitySet.workspace_path`` (a
relative path like ``this-path``) IS the URL segment. Admin serves
``<data_dir>/workspace/<workspace_path>`` directly — ``python -m http.server``
rooted at ``<data_dir>/workspace``.

  * GET /workspace/                       — root listing of <data_dir>/workspace
  * GET /workspace/this-path              — directory listing of configured workspace
  * GET /workspace/this-path/outputs/...  — nested file with MIME
  * GET /workspace/this-path/outputs/    — nested dir listing with parent link
  * GET /workspace/this-path/../../../../etc/passwd — path escape -> 403
  * GET /workspace/this-path/missing.html         — missing file -> 404
"""

from __future__ import annotations

from pathlib import Path

import httpx
import msgspec
import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.integrations import default_integration_factories
from yuubot.resources.root import Resources
from yuubot.runtime.admin import DaemonClient, build_admin_asgi_app
from yuubot.runtime.plugin_manager import ExternalPluginManager

WORKSPACE_REL = "this-path"  # matches CapabilitySet.workspace_path value


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """The configured workspace directory: ``<data_dir>/workspace/<WORKSPACE_REL>``."""
    root = tmp_path / "workspace" / WORKSPACE_REL
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


async def test_root_listing_lists_configured_workspaces(
    admin_app,
    workspace_root: Path,
) -> None:
    # workspace_root exists on disk under <data_dir>/workspace/this-path.
    # The root listing of <data_dir>/workspace should enumerate it.
    async with _client(admin_app) as client:
        response = await client.get("/workspace/")

    assert response.status_code == 200
    assert "this-path/" in response.text


async def test_directory_listing_lists_files_and_dirs(
    admin_app,
    workspace_root: Path,
) -> None:
    (workspace_root / "outputs").mkdir()
    (workspace_root / "outputs" / "chart.html").write_text(
        "<h1>chart</h1>", encoding="utf-8"
    )
    (workspace_root / ".secret").write_text("secret", encoding="utf-8")
    (workspace_root / "subdir").mkdir()

    async with _client(admin_app) as client:
        response = await client.get(f"/workspace/{WORKSPACE_REL}")

    assert response.status_code == 200
    body = response.text
    assert "outputs/" in body
    assert ".secret" in body
    assert "subdir/" in body
    # Nested file is NOT listed at this directory level.
    assert "chart.html" not in body


async def test_nested_file_response_has_mime_type(
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
            f"/workspace/{WORKSPACE_REL}/outputs/chart.html"
        )
        png = await client.get(
            f"/workspace/{WORKSPACE_REL}/outputs/pic.png"
        )

    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert html.text == "<h1>chart</h1>"

    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"


async def test_nested_directory_listing_has_parent_link(
    admin_app,
    workspace_root: Path,
) -> None:
    (workspace_root / "outputs").mkdir()
    (workspace_root / "outputs" / "chart.html").write_text(
        "x", encoding="utf-8"
    )

    async with _client(admin_app) as client:
        response = await client.get(
            f"/workspace/{WORKSPACE_REL}/outputs/"
        )

    assert response.status_code == 200
    body = response.text
    assert "chart.html" in body
    assert "../" in body  # parent link


async def test_path_escape_returns_403(admin_app) -> None:
    # Percent-encoded traversal so httpx does not normalise the .. segments
    # before the route matches.
    escapes = f"{WORKSPACE_REL}/..%2f..%2f..%2f..%2fetc%2fpasswd"
    async with _client(admin_app) as client:
        response = await client.get(f"/workspace/{escapes}")

    assert response.status_code == 403


async def test_missing_file_returns_404(
    admin_app,
    workspace_root: Path,
) -> None:
    async with _client(admin_app) as client:
        response = await client.get(
            f"/workspace/{WORKSPACE_REL}/missing.html"
        )

    assert response.status_code == 404
