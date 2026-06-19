"""E2E tests for structured Admin Conversation history."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import msgspec
import yuullm

from tests.helpers import register_test_llm_provider, make_test_daemon_infrastructure
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.runtime.daemon import YuubotDaemon, build_daemon

DAEMON_SECRET = "test-daemon-secret"
CONVERSATION_TEXT_1 = "Hello, world!"
CONVERSATION_TEXT_2 = "Python is great"
AGENT_REPLY = "admin conversation agent ack"


class ConversationProvider:
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


async def test_conversation_messages_are_persisted(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    llm = ConversationProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        async with _client(daemon) as client:
            actor = await _provision_actor(client)
            created = await _create_conversation(client, actor["id"], "history-test-1")
            await _create_conversation(client, actor["id"], "history-test-older")
            assert created["conversation_id"] == "history-test-1"
            assert created["actor_id"] == actor["id"]

            response = await _post_conversation_message(
                client,
                "history-test-1",
                CONVERSATION_TEXT_1,
            )
            assert response.status_code == 202, response.text

            messages = await _wait_for_messages(client, "history-test-1", count=2)
            assert [m["role"] for m in messages] == ["user", "assistant"]
            assert CONVERSATION_TEXT_1 in messages[0]["raw_content"]
            assert AGENT_REPLY in messages[1]["raw_content"]
            assert messages[1]["metadata"]["usage"]

            conversations = await client.get(
                "/api/admin/conversations",
                headers=_daemon_headers(),
            )
            assert conversations.status_code == 200, conversations.text
            data = conversations.json()["data"]
            ids = [item["conversation_id"] for item in data]
            assert ids[0] == "history-test-1", data
            assert "history-test-1" in ids

        assert len(llm.calls) == 1
        assert yuullm.render_message_text(llm.calls[0][-1]) == CONVERSATION_TEXT_1
    finally:
        await daemon.stop()


async def test_conversation_agent_reuses_persisted_history(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    llm = ConversationProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        async with _client(daemon) as client:
            actor = await _provision_actor(client)
            await _create_conversation(client, actor["id"], "history-test-2")

            first = await _post_conversation_message(
                client,
                "history-test-2",
                CONVERSATION_TEXT_1,
            )
            assert first.status_code == 202, first.text
            await _wait_for_messages(client, "history-test-2", count=2)

            await daemon.actors.stop_actor(actor["id"])
            await daemon.actors.start_actor(actor["id"])
            await asyncio.sleep(0.05)

            second = await _post_conversation_message(
                client,
                "history-test-2",
                CONVERSATION_TEXT_2,
            )
            assert second.status_code == 202, second.text
            messages = await _wait_for_messages(client, "history-test-2", count=4)

        assert [m["role"] for m in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert len(llm.calls) == 2
        second_call_text = [yuullm.render_message_text(item) for item in llm.calls[1]]
        assert CONVERSATION_TEXT_1 in second_call_text
        assert AGENT_REPLY in second_call_text
        assert CONVERSATION_TEXT_2 in second_call_text
    finally:
        await daemon.stop()


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


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://daemon.test",
    )


def _daemon_headers() -> dict[str, str]:
    return {"X-Daemon-Secret": DAEMON_SECRET}


async def _provision_actor(client: httpx.AsyncClient) -> dict[str, Any]:
    backend = await _create_llm_backend(client)
    character = await _create_character(client)
    capability_set = await _create_capability_set(client)
    return await _create_actor(client, character["id"], capability_set["id"], backend["id"])


async def _create_llm_backend(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/llm-backends",
        json={
            "name": "conversation-openai",
            "yuuagents_provider": "openai",
            "model_capabilities": {"chat": True, "tool_calling": False},
            "models": {"names": ["gpt-4o"]},
            "pricing": {"entries": []},
            "budget": {"daily_usd": 0},
            "provider_options": {"base_url": "http://llm.test/v1"},
            "default_model": "gpt-4o",
        },
        headers=_daemon_headers(),
    )
    return _created(response)


async def _create_character(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/characters",
        json={
            "name": "conversation-helper",
            "description": "E2E conversation helper",
            "system_prompt": "You are an E2E admin conversation agent.",
            "facade_module": "yb",
            "default_hints": {"language": "zh-CN", "tone": "friendly"},
        },
        headers=_daemon_headers(),
    )
    return _created(response)


async def _create_capability_set(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/capability-sets",
        json={
            "name": "conversation-capabilities",
            "description": "E2E conversation capability set",
        },
        headers=_daemon_headers(),
    )
    return _created(response)


async def _create_actor(
    client: httpx.AsyncClient,
    character_id: str,
    capability_set_id: str,
    backend_id: str,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/actors",
        json={
            "name": "conversation-actor",
            "type": "simple_loop",
            "default_model": "gpt-4o",
            "default_character_id": character_id,
            "capability_set_id": capability_set_id,
            "default_llm_backend_id": backend_id,
            "default_budget": {"max_steps": 4},
            "enabled": True,
        },
        headers=_daemon_headers(),
    )
    return _created(response)


async def _create_conversation(
    client: httpx.AsyncClient,
    actor_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    response = await client.post(
        "/api/admin/conversations",
        json={
            "conversation_id": conversation_id,
            "actor_id": actor_id,
        },
        headers=_daemon_headers(),
    )
    body = response.json()
    assert response.status_code == 201, body
    assert body["status"] == "ok", body
    return body["data"]


async def _post_conversation_message(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
) -> httpx.Response:
    return await client.post(
        f"/api/admin/conversations/{conversation_id}/messages",
        json={"text": text},
        headers=_daemon_headers(),
    )


async def _wait_for_messages(
    client: httpx.AsyncClient,
    conversation_id: str,
    *,
    count: int,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        response = await client.get(
            f"/api/admin/conversations/{conversation_id}/messages",
            headers=_daemon_headers(),
        )
        assert response.status_code == 200, response.text
        messages = response.json()["data"]
        if len(messages) >= count:
            return messages
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected {count} conversation messages")
        await asyncio.sleep(0.01)


def _created(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert response.status_code == 201, body
    assert body["status"] == "ok", body
    data = body["data"]
    assert isinstance(data, dict)
    assert data["id"]
    return data
