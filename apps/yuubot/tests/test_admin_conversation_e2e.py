"""E2E coverage for provisioning an actor and using Admin Conversation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest
import yuullm
import yuutrace
from yuutrace import MemoryTraceStore

from tests.helpers import register_test_llm_provider, make_test_daemon_infrastructure
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.integrations import default_integration_factories
from yuubot.runtime.admin.app import DaemonClient, build_admin_asgi_app
from yuubot.runtime.admin.handlers import DaemonResponse
from yuubot.runtime.daemon import YuubotDaemon, build_daemon
from yuubot.runtime.plugin_manager import ExternalPluginManager


CONVERSATION_TEXT = "你好，admin conversation agent"
AGENT_REPLY = "admin conversation agent 已收到"


async def test_user_can_create_actor_and_work_through_admin_conversation(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = AdminConversationProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    store = yuutrace.init_memory()
    try:
        await _connect_admin_proxy_to_daemon(monkeypatch, daemon)
        admin_app = _build_admin_app(daemon, yuubot_config, tmp_path)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_app),
            base_url="http://admin.test",
        ) as client:
            backend = await _create_llm_backend(client)
            capability_set = await _create_capability_set(client)
            actor = await _create_actor(
                client, capability_set["id"], backend["id"]
            )

            conversation_page = await client.get("/admin/conversations")
            assert conversation_page.status_code == 200
            assert "yuubot test admin" in conversation_page.text

            conversation = await client.post(
                "/api/admin/conversations/admin-conversation-e2e/messages",
                json={
                    "text": CONVERSATION_TEXT,
                    "actor_id": actor["id"],
                },
            )
            messages = await _conversation_messages(client, "admin-conversation-e2e")

        trace_conversation = await _trace_conversation(
            store,
            "admin-conversation-e2e",
        )
        assert conversation.status_code == 202, conversation.text
        body = conversation.json()
        assert body["status"] == "accepted"
        assert body["data"]["conversation_id"] == "admin-conversation-e2e"

        assert len(llm.calls) == 1
        rendered_user_message = yuullm.render_message_text(llm.calls[0][-1])
        assert rendered_user_message == CONVERSATION_TEXT
        system_prompt = yuullm.render_message_text(llm.calls[0][0])
        assert "No integration SDKs configured." in system_prompt
        assert "You are an E2E admin conversation agent." in system_prompt
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert CONVERSATION_TEXT in messages[0]["raw_content"]
        assert AGENT_REPLY in messages[1]["raw_content"]
        assert trace_conversation["id"] == "admin-conversation-e2e"
        assert any(
            span["conversation_id"] == "admin-conversation-e2e"
            for span in trace_conversation["spans"]
        )
    finally:
        await daemon.stop()


async def test_admin_spa_entry_revalidates_to_avoid_stale_chunks(    yuubot_config: BootstrapConfig,
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


async def test_admin_conversation_reports_missing_model_pricing_as_configuration_error(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        await _connect_admin_proxy_to_daemon(monkeypatch, daemon)
        admin_app = _build_admin_app(daemon, yuubot_config, tmp_path)

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
            capability_set = await _create_capability_set(client)

            response = await client.post(
                "/api/resources/actors",
                json={
                    "name": "admin-conversation-actor",
                    "type": "simple_loop",
                    "model": "deepseek-v4-flash",
                    "persona_prompt": "You are an E2E admin conversation agent.",
                    "capability_set_id": capability_set["id"],
                    "llm_backend_id": backend["id"],
                    "per_run_budget": {"max_usd": 1},
                    "enabled": True,
                },
            )

        assert response.status_code == 400, response.text
        body = response.json()
        assert body["status"] == "error"
        assert body["code"] == "configuration_error"
        assert "deepseek-v4-flash" in body["detail"]
        assert "deepseek-main" in body["detail"]
    finally:
        await daemon.stop()


class AdminConversationProvider:
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
            yield yuullm.Response({"type": "text", "text": AGENT_REPLY})

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
        response = await client.get(
            f"/api/admin/conversations/{conversation_id}/messages"
        )
        assert response.status_code == 200, response.text
        messages = response.json()["data"]
        if len(messages) >= 2:
            return messages
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected conversation {conversation_id} messages")
        await asyncio.sleep(0.01)


async def _trace_conversation(
    store: MemoryTraceStore,
    conversation_id: str,
    *,
    timeout_s: float = 5.0,
):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        conversation = store.get_conversation(conversation_id)
        if conversation is not None:
            return conversation
        if asyncio.get_running_loop().time() >= deadline:
            spans = store.get_all_spans()
            raise AssertionError(
                f"expected trace conversation {conversation_id}, got {spans}"
            )
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
        components=make_test_daemon_infrastructure(),
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
    name: str = "admin-conversation-openai",
    provider: str = "openai",
    model: str = "gpt-4o",
    daily_budget: float = 0,
    pricing_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/llm-backends",
        json={
            "name": name,
            "provider_identity": provider,
            "model_configs": _model_configs_payload(model, pricing_entries),
            "budget": {"daily_usd": daily_budget},
            "provider_options": {"base_url": "http://llm.test/v1"},
        },
    )
    return _created(response)


def _model_configs_payload(
    model: str,
    pricing_entries: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if pricing_entries == []:
        return {}
    pricing = pricing_entries[0] if pricing_entries else {}
    return {
        model: {
            "capabilities": {"chat": True, "tool_calling": False},
            "pricing": pricing,
        }
    }


async def _create_capability_set(
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/capability-sets",
        json={
            "name": "admin-conversation-capabilities",
            "description": "E2E admin conversation capability set",
        },
    )
    return _created(response)


async def _create_actor(
    client: httpx.AsyncClient,
    capability_set_id: str,
    backend_id: str,
    *,
    model: str = "gpt-4o",
    enabled: bool = True,
    allow_partial: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/actors",
        json={
            "name": "admin-conversation-actor",
            "type": "simple_loop",
            "model": model,
            "persona_prompt": "You are an E2E admin conversation agent.",
            "capability_set_id": capability_set_id,
            "llm_backend_id": backend_id,
            "per_run_budget": {"max_steps": 4},
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
