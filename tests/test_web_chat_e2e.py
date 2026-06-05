"""E2E coverage for provisioning an actor and chatting through the admin API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest
import yuullm

from helpers import register_test_llm_provider
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.integrations import default_integration_factories
from yuubot.runtime.admin.app import (
    DaemonClient,
    DaemonResponse,
    build_admin_asgi_app,
)
from yuubot.runtime.daemon import YuubotDaemon, build_daemon
from yuubot.runtime.plugin_manager import ExternalPluginManager


CHAT_TEXT = "你好，web chat actor"
ACTOR_REPLY = "web chat actor 已收到"


async def test_user_can_create_actor_and_chat_through_admin_web_path(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = WebChatProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        admin_app = _build_admin_app(daemon, yuubot_config, tmp_path)
        await _connect_admin_proxy_to_daemon(monkeypatch, daemon)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_app),
            base_url="http://admin.test",
        ) as client:
            backend = await _create_llm_backend(client)
            character = await _create_character(client)
            actor = await _create_actor(client, character["id"], backend["id"])

            chat_page = await client.get("/chat")
            assert chat_page.status_code == 200
            assert "yuubot test admin" in chat_page.text

            created = await client.post(
                "/api/conversations",
                json={
                    "conversation_id": "web-chat-e2e",
                    "actor_id": actor["id"],
                },
            )
            assert created.status_code == 201, created.text

            chat = await client.post(
                "/api/conversations/web-chat-e2e/messages",
                json={
                    "text": CHAT_TEXT,
                },
            )
            messages = await _conversation_messages(client, "web-chat-e2e")
            legacy_dialogs = await client.get("/api/chat/dialogs")

        assert chat.status_code == 202, chat.text
        body = chat.json()
        assert body["status"] == "accepted"
        assert body["data"]["conversation_id"] == "web-chat-e2e"

        assert len(llm.calls) == 1
        rendered_user_message = yuullm.render_message_text(llm.calls[0][-1])
        assert rendered_user_message == CHAT_TEXT
        assert yuullm.render_message_text(llm.calls[0][0]) == "You are an E2E web chat actor."
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert CHAT_TEXT in messages[0]["raw_content"]
        assert ACTOR_REPLY in messages[1]["raw_content"]
        assert legacy_dialogs.status_code == 200, legacy_dialogs.text
        assert legacy_dialogs.json()["data"] == []
    finally:
        await daemon.stop()


async def test_admin_spa_entry_revalidates_to_avoid_stale_chunks(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    daemon = await _build_daemon(yuubot_config, tmp_path)
    try:
        admin_app = _build_admin_app(daemon, yuubot_config, tmp_path)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_app),
            base_url="http://admin.test",
        ) as client:
            response = await client.get("/providers/backend-1")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert "yuubot test admin" in response.text
    finally:
        await daemon.stop()


async def test_web_chat_reports_missing_model_pricing_as_configuration_error(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        admin_app = _build_admin_app(daemon, yuubot_config, tmp_path)
        await _connect_admin_proxy_to_daemon(monkeypatch, daemon)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_app),
            base_url="http://admin.test",
        ) as client:
            backend = await _create_llm_backend(
                client,
                name="deepseek-main",
                provider="deepseek",
                model="deepseek-v4-flash",
                daily_budget=1,
                pricing_entries=[],
            )
            character = await _create_character(client)
            actor = await _create_actor(
                client,
                character["id"],
                backend["id"],
                model="deepseek-v4-flash",
                allow_partial=True,
            )

            created = await client.post(
                "/api/conversations",
                json={
                    "conversation_id": "web-chat-pricing-error",
                    "actor_id": actor["id"],
                },
            )
            assert created.status_code == 201, created.text

            chat = await client.post(
                "/api/conversations/web-chat-pricing-error/messages",
                json={
                    "text": CHAT_TEXT,
                },
            )

        assert chat.status_code == 400, chat.text
        body = chat.json()
        assert body["status"] == "error"
        assert body["code"] == "configuration_error"
        assert "deepseek-v4-flash" in body["detail"]
        assert "deepseek-main" in body["detail"]
        assert "pricing.entries" in body["hint"]
    finally:
        await daemon.stop()


class WebChatProvider:
    def __init__(self) -> None:
        self.calls: list[list[yuullm.Message]] = []

    @property
    def api_type(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "scripted"

    async def list_models(self) -> list[yuullm.ProviderModel]:
        return [yuullm.ProviderModel(id="gpt-4o")]

    async def stream(
        self,
        history: yuullm.History,
        *,
        model: str,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        _ = model, on_raw_chunk, kwargs
        messages, _tools = yuullm.split_history(history)
        self.calls.append(list(messages))

        async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": ACTOR_REPLY})

        return stream_items(), yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model="gpt-4o",
                input_tokens=1,
                output_tokens=1,
            )
        )


async def _conversation_messages(
    client: httpx.AsyncClient,
    conversation_id: str,
    *,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        response = await client.get(f"/api/conversations/{conversation_id}/messages")
        assert response.status_code == 200, response.text
        messages = response.json()["data"]
        if len(messages) >= 2:
            return messages
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected conversation {conversation_id} messages")
        await asyncio.sleep(0.01)


async def _build_daemon(
    base_config: BootstrapConfig,
    tmp_path: Path,
) -> YuubotDaemon:
    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(data_dir=str(tmp_path / "data")),
        ),
    )


def _build_admin_app(
    daemon: YuubotDaemon,
    base_config: BootstrapConfig,
    tmp_path: Path,
):
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        "<!doctype html><title>yuubot test admin</title>",
        encoding="utf-8",
    )
    return build_admin_asgi_app(
        config=msgspec.structs.replace(
            base_config.admin,
            web_dist_dir=str(web_dist),
        ),
        resources=daemon.resources,
        daemon=DaemonClient(
            base_url="http://daemon.test",
            daemon_secret=base_config.server.daemon_secret,
        ),
        integration_factories=default_integration_factories(),
        plugin_manager=ExternalPluginManager(
            plugins_dir=tmp_path / "plugins",
            data_root=tmp_path / "plugin-data",
        ),
    )


async def _connect_admin_proxy_to_daemon(
    monkeypatch: pytest.MonkeyPatch,
    daemon: YuubotDaemon,
) -> None:
    daemon_app = daemon.asgi_app()

    async def request_daemon(
        daemon_client: DaemonClient,
        path: str,
        *,
        method: str,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> DaemonResponse:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=daemon_app),
            base_url=daemon_client.base_url,
        ) as client:
            response = await client.request(
                method,
                path,
                content=body if body else None,
                headers={
                    "Content-Type": content_type,
                    "X-Daemon-Secret": daemon_client.daemon_secret,
                },
            )
        return DaemonResponse(
            status_code=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type", "application/json"),
        )

    monkeypatch.setattr("yuubot.runtime.admin.app._request_daemon", request_daemon)


async def _create_llm_backend(
    client: httpx.AsyncClient,
    *,
    name: str = "web-chat-openai",
    provider: str = "openai",
    model: str = "gpt-4o",
    daily_budget: float = 0,
    pricing_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/llm-backends",
        json={
            "name": name,
            "yuuagents_provider": provider,
            "model_capabilities": {"chat": True, "tool_calling": False},
            "models": {"names": [model]},
            "pricing": {"entries": pricing_entries or []},
            "budget": {"daily_usd": daily_budget},
            "provider_options": {"base_url": "http://llm.test/v1"},
            "default_model": model,
        },
    )
    return _created(response)


async def _create_character(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/characters",
        json={
            "name": "web-chat-helper",
            "description": "E2E web chat helper",
            "system_prompt": "You are an E2E web chat actor.",
            "facade_module": "yb",
            "default_hints": {"language": "zh-CN", "tone": "friendly"},
        },
    )
    return _created(response)


async def _create_actor(
    client: httpx.AsyncClient,
    character_id: str,
    backend_id: str,
    *,
    model: str = "gpt-4o",
    enabled: bool = True,
    allow_partial: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/actors",
        json={
            "name": "web-chat-actor",
            "type": "simple_loop",
            "model": model,
            "character_id": character_id,
            "llm_backend_id": backend_id,
            "max_steps": 4,
            "daily_budget": 0,
            "enabled": enabled,
        },
    )
    if allow_partial:
        return _created_or_partial(response)
    return _created(response)


def _created(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert response.status_code == 201, body
    assert body["status"] == "ok", body
    data = body["data"]
    assert isinstance(data, dict)
    assert data["id"]
    return data


def _created_or_partial(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert response.status_code in (200, 201), body
    assert body["status"] in ("ok", "partial"), body
    data = body["data"]
    assert isinstance(data, dict)
    assert data["id"]
    return data
