from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import pytest
from yuubot.runtime.logging_config import LOG_FILENAME, configure_logging
from yuubot.runtime.shares import ShareRegistry
from yuubot.web.errors import INTERNAL_ERROR_MESSAGE

from support.api import base_url, boot_app, running_server, bootstrap, enable_actor, http_json, put_actor, put_provider


@pytest.mark.asyncio
async def test_bootstrap_development_flag(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "prod")
    async with running_server(app, False) as server:
        snapshot = await bootstrap(server)
        assert snapshot["development"] is False

    dev_app = await boot_app(tmp_path / "dev")
    async with running_server(dev_app, True) as server:
        snapshot = await bootstrap(server)
        assert snapshot["development"] is True


@pytest.mark.asyncio
async def test_configure_logging_creates_log_file(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    log_path = configure_logging(logs_dir, False, 1024, 2)
    assert log_path == logs_dir / LOG_FILENAME
    assert log_path.is_file()
    assert any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logging.getLogger().handlers)


@pytest.mark.asyncio
async def test_internal_error_sanitized_outside_development(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await boot_app(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")

    async def broken_publish(
        self,
        actor_id: str,
        source_path: str,
        expires_at: str | None,
    ) -> object:
        del self, actor_id, source_path, expires_at
        raise OSError("secret leak /var/run/detail")

    monkeypatch.setattr(ShareRegistry, "publish", broken_publish)

    async with running_server(app, False) as server:
        await put_provider(server)
        await put_actor(server, "actor-a", workspace=workspace)
        await enable_actor(server, "actor-a")
        payload = await http_json(
            "POST",
            f"{base_url(server)}/api/shares",
            {"actor_id": "actor-a", "source_path": "note.txt"},
            expected_status=500,
        )
        error = cast(dict[str, object], payload["error"])
        assert error["code"] == "internal_error"
        assert error["message"] == INTERNAL_ERROR_MESSAGE
        assert "detail" not in error


@pytest.mark.asyncio
async def test_internal_error_passthrough_in_development(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await boot_app(tmp_path / "data")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")

    async def broken_publish(
        self,
        actor_id: str,
        source_path: str,
        expires_at: str | None,
    ) -> object:
        del self, actor_id, source_path, expires_at
        raise OSError("secret leak /var/run/detail")

    monkeypatch.setattr(ShareRegistry, "publish", broken_publish)

    async with running_server(app, True) as server:
        await put_provider(server)
        await put_actor(server, "actor-a", workspace=workspace)
        await enable_actor(server, "actor-a")
        payload = await http_json(
            "POST",
            f"{base_url(server)}/api/shares",
            {"actor_id": "actor-a", "source_path": "note.txt"},
            expected_status=500,
        )
        error = cast(dict[str, object], payload["error"])
        assert error["code"] == "internal_error"
        assert error["message"] == "secret leak /var/run/detail"
        detail = cast(dict[str, object], error["detail"])
        assert detail["type"] == "OSError"
