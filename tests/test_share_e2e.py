from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest

from yuubot import Yuubot
from support.api import (
    SharedTestContext,
    base_url,
    http_json,
)
from support.llm_fakes import scripted_reply


async def test_http_share_publish_public_read_and_revoke(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    site = test_context.workspace / "reports" / "q3"
    site.mkdir(parents=True)
    (site / "summary.txt").write_text("q3 summary", encoding="utf-8")

    grant = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/shares",
        {"actor_id": actor_id, "source_path": "reports/q3"},
        expected_status=201,
    )
    share_id = cast(str, grant["id"])
    published_path = published_share_path(test_context, share_id)
    assert published_path.is_dir()
    public_url = f"{base_url(test_context.server)}/s/{share_id}/summary.txt"

    async with httpx.AsyncClient() as client:
        response = await client.get(public_url, timeout=10.0)
        partial = await client.get(public_url, headers={"Range": "bytes=0-1"}, timeout=10.0)
    assert response.status_code == 200
    assert response.text == "q3 summary"
    assert partial.status_code == 206
    assert partial.text == "q3"
    assert partial.headers["content-range"].startswith("bytes 0-1/")

    await http_json("DELETE", f"{base_url(test_context.server)}/api/shares/{share_id}")
    async with httpx.AsyncClient() as client:
        response = await client.get(public_url, timeout=10.0)
    assert response.status_code == 404
    assert not published_path.exists()
    shares = await http_json("GET", f"{base_url(test_context.server)}/api/shares")
    share_ids = {item["id"] for item in cast(list[dict[str, object]], shares["items"])}
    assert share_id not in share_ids


async def test_http_share_directory_without_index_lists_files(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    directory = test_context.workspace / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "AGENTS.md").write_text("notes", encoding="utf-8")

    grant = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/shares",
        {"actor_id": actor_id, "source_path": "artifacts"},
        expected_status=201,
    )
    share_id = cast(str, grant["id"])

    async with httpx.AsyncClient() as client:
        listing = await client.get(f"{base_url(test_context.server)}/s/{share_id}/", timeout=10.0)
        file_response = await client.get(f"{base_url(test_context.server)}/s/{share_id}/AGENTS.md", timeout=10.0)
    assert listing.status_code == 200
    assert listing.headers["content-type"].startswith("text/html")
    assert "AGENTS.md" in listing.text
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("text/")
    assert file_response.text == "notes"


async def test_http_share_publish_file_public_read_and_revoke(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    test_context.workspace.mkdir(exist_ok=True)
    page = test_context.workspace / "report.html"
    page.write_text("<h1>report</h1>", encoding="utf-8")

    grant = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/shares",
        {"actor_id": actor_id, "source_path": "report.html"},
        expected_status=201,
    )
    share_id = cast(str, grant["id"])
    published_path = published_share_path(test_context, share_id)
    assert grant["kind"] == "file"
    assert grant["entry_path"] == "report.html"
    assert (published_path / "report.html").is_file()

    async with httpx.AsyncClient() as client:
        direct = await client.get(f"{base_url(test_context.server)}/s/{share_id}/report.html", timeout=10.0)
        root = await client.get(f"{base_url(test_context.server)}/s/{share_id}", timeout=10.0)
    assert direct.status_code == 200
    assert direct.text == "<h1>report</h1>"
    assert root.status_code == 200
    assert root.text == "<h1>report</h1>"

    await http_json("DELETE", f"{base_url(test_context.server)}/api/shares/{share_id}")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url(test_context.server)}/s/{share_id}/report.html", timeout=10.0)
    assert response.status_code == 404
    assert not published_path.exists()


def test_share_resolves_nested_file_when_data_dir_is_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yuubot.runtime.shares import ShareGrant, ShareRegistry

    class State:
        async def load_share_grants(self) -> list[ShareGrant]:
            return []

        async def put_share_grant(self, grant: ShareGrant) -> None:
            del grant

        async def delete_share_grant(self, share_id: str) -> None:
            del share_id

    def emit(event: str, **payload: object) -> None:
        del event, payload

    monkeypatch.chdir(tmp_path)
    published = tmp_path / ".yuubot-data" / "published" / "sh-relative"
    published.mkdir(parents=True)
    published.joinpath("AGENTS.md").write_text("notes", encoding="utf-8")
    registry = ShareRegistry(data_dir=Path(".yuubot-data"), state=State(), emit=emit)
    registry._grants["sh-relative"] = ShareGrant(
        id="sh-relative",
        actor_id="amy",
        source_path="AGENTS.md",
        created_at="2026-01-01T00:00:00+00:00",
        expires_at=None,
        kind="file",
        entry_path="AGENTS.md",
    )

    assert registry.resolve_file("sh-relative", "AGENTS.md").read_text(encoding="utf-8") == "notes"


async def test_http_share_publish_validation_errors(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/shares",
        {"actor_id": actor_id, "source_path": "../outside"},
        expected_status=400,
    )


async def test_http_share_expired(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    brief = test_context.workspace / "brief"
    brief.mkdir()
    (brief / "index.html").write_text("brief", encoding="utf-8")
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    grant = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/shares",
        {"actor_id": actor_id, "source_path": "brief", "expires_at": expired_at},
        expected_status=201,
    )
    assert grant["expires_at"] == expired_at
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url(test_context.server)}/s/{grant['id']}", timeout=10.0)
    assert response.status_code == 404


def published_share_path(test_context: SharedTestContext, share_id: str) -> Path:
    app = getattr(test_context.server, "app", None)
    assert isinstance(app, Yuubot)
    return app.runtime.shares.published_dir / share_id
