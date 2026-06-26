"""E2E tests for conversation-owned model history.

The conversation row and its history are created together on the first
real send. Before the first send there is no server-side conversation and
no agent — draft conversations are frontend-only. After the first send,
the persisted prompt prefix (tool specs + system message) is frozen:
restarts, idle expiry, and AGENTS.md mutations never reach the persisted
prefix.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import msgspec
import yuullm

from tests.helpers import (
    insert_echo_actor_resources,
    make_test_daemon_infrastructure,
    register_test_llm_provider,
)
from tests.llm_prompt.framework import PromptCapture
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.resources.store.models import CapabilitySetORM
from yuubot.runtime.daemon import YuubotDaemon, build_daemon

AGENT_REPLY = "admin conversation agent ack"
CONVERSATION_TEXT_1 = "Hello, world!"
CONVERSATION_TEXT_2 = "Python is great"


def _headers(config: BootstrapConfig) -> dict[str, str]:
    return {"X-Daemon-Secret": config.server.daemon_secret}


def _client(daemon_app: Any, config: BootstrapConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon_app),
        base_url="http://daemon.test",
        headers=_headers(config),
    )


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


async def _provision_actor(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    *,
    suffix: str = "default",
) -> dict[str, Any]:
    backend = await _create_llm_backend(client, config, suffix=suffix)
    capability_set = await _create_capability_set(client, config, suffix=suffix)
    return await _create_actor(
        client,
        config,
        capability_set["id"],
        backend["id"],
        suffix=suffix,
    )


async def _create_llm_backend(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    *,
    suffix: str,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/llm-backends",
        json={
            "name": f"conversation-openai-{suffix}",
            "provider_identity": "openai",
            "model_configs": {
                "gpt-4o": {
                    "capabilities": {"chat": True, "tool_calling": False},
                    "pricing": {},
                }
            },
            "budget": {"daily_usd": 0},
            "provider_options": {"base_url": "http://llm.test/v1"},
            "recommended_model": "gpt-4o",
        },
        headers=_headers(config),
    )
    return _created(response)


async def _create_capability_set(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    *,
    suffix: str,
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/capability-sets",
        json={
            "name": f"conversation-capabilities-{suffix}",
            "description": "E2E conversation capability set",
            "integration_capability_ids": [],
        },
        headers=_headers(config),
    )
    return _created(response)


async def _create_actor(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    capability_set_id: str,
    backend_id: str,
    *,
    suffix: str = "default",
) -> dict[str, Any]:
    response = await client.post(
        "/api/resources/actors",
        json={
            "name": f"conversation-actor-{suffix}",
            "type": "simple_loop",
            "model": "gpt-4o",
            "persona_prompt": "You are an E2E admin conversation agent.",
            "capability_set_id": capability_set_id,
            "llm_backend_id": backend_id,
            "per_run_budget": {"max_steps": 4},
            "enabled": True,
        },
        headers=_headers(config),
    )
    return _created(response)


async def _send_message(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    conversation_id: str,
    text: str,
    *,
    actor_id: str,
) -> httpx.Response:
    return await client.post(
        f"/api/admin/conversations/{conversation_id}/messages",
        json={"text": text, "actor_id": actor_id},
        headers=_headers(config),
    )


async def _wait_for_messages(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    conversation_id: str,
    *,
    count: int,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        response = await client.get(
            f"/api/admin/conversations/{conversation_id}/messages",
            headers=_headers(config),
        )
        assert response.status_code == 200, response.text
        messages = response.json()["data"]
        if len(messages) >= count:
            return messages
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"expected {count} conversation messages, got {len(messages)}"
            )
        await asyncio.sleep(0.01)


async def _get_model_history(
    client: httpx.AsyncClient,
    config: BootstrapConfig,
    conversation_id: str,
) -> dict[str, Any]:
    response = await client.get(
        f"/api/admin/conversations/{conversation_id}/model-history",
        headers=_headers(config),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body
    return body["data"]


def _created(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert response.status_code == 201, body
    assert body["status"] == "ok", body
    data = body["data"]
    assert isinstance(data, dict)
    assert data["id"]
    return data


def _captured_system_text(captured_messages: list[yuullm.Message]) -> str:
    for message in captured_messages:
        if message.role == "system":
            return yuullm.render_message_text(message)
    raise AssertionError(
        "no system message captured in the LLM call history; "
        f"roles seen: {[m.role for m in captured_messages]}"
    )


def _render_message_text(item_payload: dict[str, Any]) -> str:
    content = item_payload.get("content") or []
    parts: list[str] = []
    for content_item in content:
        if isinstance(content_item, dict) and content_item.get("type") == "text":
            parts.append(str(content_item.get("text") or ""))
    return "".join(parts)


class ConversationProvider:
    """Scripted LLM provider that records every call's full history."""

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


async def _insert_echo_actor_with_workspace(
    daemon: YuubotDaemon,
    *,
    actor_id: str,
    workspace_path: str,
    system_prompt: str = "You are a freeze verification agent.",
) -> None:
    repository = daemon.resources.repository
    resources = await insert_echo_actor_resources(
        repository,
        actor_id=actor_id,
        system_prompt=system_prompt,
    )
    await repository.update(
        CapabilitySetORM,
        resources.actor.capability_set_id,
        workspace_path=workspace_path,
    )


# ---------------------------------------------------------------------------
# Red tests
# ---------------------------------------------------------------------------


async def test_first_send_creates_conversation_and_ordered_history(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    """Red test 1.

    First send creates the conversation row and persists ordered history
    items ``[tool_specs?, system_message, user_message]``. The persisted
    ``tool_specs`` and system message match what the scripted LLM received
    in ``PromptCapture.calls[0]``.
    """
    capture = PromptCapture()
    register_test_llm_provider("openai", capture)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        async with _client(daemon.asgi_app(), yuubot_config) as client:
            actor = await _provision_actor(client, yuubot_config, suffix="ordered")
            conversation_id = "history-test-ordered"

            response = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_1,
                actor_id=actor["id"],
            )
            assert response.status_code == 202, response.text
            await _wait_for_messages(
                client, yuubot_config, conversation_id, count=2
            )

        await capture.wait_for_calls(1)
        captured_messages = capture.calls[0]

        # Fresh ASGI app — no shared in-memory agent cache — to verify the
        # persisted state matches what the LLM saw.
        async with _client(daemon.asgi_app(), yuubot_config) as client:
            history = await _get_model_history(
                client, yuubot_config, conversation_id
            )

        items = history["history"]
        if items[0]["item_kind"] == "tools":
            tools_row = items[0]
            system_row = items[1]
            user_row = items[2]
            assert tools_row["item"]["type"] == "tools"
            assert tools_row["item"]["tools"] == list(capture.tools[0])
        else:
            system_row = items[0]
            user_row = items[1]
        assert system_row["item_kind"] == "message"
        assert system_row["item"]["role"] == "system"
        assert user_row["item_kind"] == "message"
        assert user_row["item"]["role"] == "user"

        assert _captured_system_text(captured_messages) == _render_message_text(
            system_row["item"]
        )
        assert _render_message_text(user_row["item"]) == CONVERSATION_TEXT_1
    finally:
        await daemon.stop()


async def test_draft_uuid_returns_404_on_model_history(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    """Red test 2.

    GET /model-history for a UUID that was never sent returns 404 — there
    is no server-side conversation row and no history to project.
    """
    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        async with _client(daemon.asgi_app(), yuubot_config) as client:
            await _provision_actor(client, yuubot_config, suffix="draft")
            response = await client.get(
                "/api/admin/conversations/draft-only-uuid/model-history",
                headers=_headers(yuubot_config),
            )

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["status"] == "error"
    finally:
        await daemon.stop()


async def test_restart_preserves_system_message_across_agents_md_mutation(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    """Red test 3.

    After a simulated runtime-restart cache drop plus an ``AGENTS.md``
    mutation between turns, the system message captured on the next turn
    is unchanged and still contains the v1 marker, not the v2 marker
    (freeze holds across restart).
    """
    capture = PromptCapture()
    register_test_llm_provider("openai", capture)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        await _insert_echo_actor_with_workspace(
            daemon,
            actor_id="freeze-actor",
            workspace_path="freeze-ws",
        )
        await daemon.resources.event_bus.drain()

        workspace_dir = tmp_path / "data" / "workspace" / "freeze-ws"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        agents_md = workspace_dir / "AGENTS.md"
        agents_md.write_text("__MARKER_V1__\n", encoding="utf-8")

        async with _client(daemon.asgi_app(), yuubot_config) as client:
            response = await _send_message(
                client,
                yuubot_config,
                "freeze-conversation",
                "first turn",
                actor_id="freeze-actor",
            )
            assert response.status_code == 202, response.text

        await capture.wait_for_calls(1)
        system_1 = _captured_system_text(capture.calls[0])

        # Mutate AGENTS.md between turns. The persisted prefix must win.
        agents_md.write_text("__MARKER_V2__\n", encoding="utf-8")
        await asyncio.sleep(0.01)

        # Fresh ASGI app → fresh ConversationManager with empty
        # ``_runtimes``. The next send falls into the restart branch and
        # replays the persisted prefix from conversation_history_items.
        async with _client(daemon.asgi_app(), yuubot_config) as client:
            response = await _send_message(
                client,
                yuubot_config,
                "freeze-conversation",
                "second turn",
                actor_id="freeze-actor",
            )
            assert response.status_code == 202, response.text

        await capture.wait_for_calls(2)
        system_2 = _captured_system_text(capture.calls[1])
    finally:
        await daemon.stop()

    assert system_1 == system_2, "system message changed across restart"
    assert "__MARKER_V1__" in system_1
    assert "__MARKER_V2__" not in system_1


async def test_subsequent_send_reuses_in_memory_agent(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    """Red test 4.

    Subsequent sends on an alive agent reuse the in-memory agent. The
    LLM sees the cached history on the second call (same system message
    + both user turns + assistant reply), and no extra system-message
    rebuild happens.
    """
    llm = ConversationProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    app = daemon.asgi_app()
    await daemon.start()
    try:
        async with _client(app, yuubot_config) as client:
            actor = await _provision_actor(client, yuubot_config, suffix="hot")
            conversation_id = "history-test-hot"

            first = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_1,
                actor_id=actor["id"],
            )
            assert first.status_code == 202, first.text
            await _wait_for_messages(
                client, yuubot_config, conversation_id, count=2
            )

            second = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_2,
                actor_id=actor["id"],
            )
            assert second.status_code == 202, second.text
            await _wait_for_messages(
                client, yuubot_config, conversation_id, count=4
            )

        assert len(llm.calls) == 2
        # Cache reuse: the second LLM call sees the same system message
        # AND the entire first turn (user1 + assistant1) followed by
        # user2. The system message is identical across calls.
        system_1 = _captured_system_text(llm.calls[0])
        system_2 = _captured_system_text(llm.calls[1])
        assert system_1 == system_2

        rendered_second_call = [
            yuullm.render_message_text(item) for item in llm.calls[1]
        ]
        assert CONVERSATION_TEXT_1 in rendered_second_call
        assert AGENT_REPLY in rendered_second_call
        assert CONVERSATION_TEXT_2 in rendered_second_call
    finally:
        await daemon.stop()


async def test_subsequent_send_with_conflicting_actor_returns_409(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    """Red test 5.

    First send creates a conversation bound to actor A. A second send
    to the same conversation UUID supplying ``actor_id=B`` returns 409
    ``conversation_binding_conflict`` with the persisted actor A binding
    in ``data``.
    """
    llm = ConversationProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    app = daemon.asgi_app()
    await daemon.start()
    try:
        async with _client(app, yuubot_config) as client:
            first_actor = await _provision_actor(
                client, yuubot_config, suffix="locked-first"
            )
            second_actor = await _provision_actor(
                client, yuubot_config, suffix="locked-second"
            )
            conversation_id = "history-locked-binding"

            response = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_1,
                actor_id=first_actor["id"],
            )
            assert response.status_code == 202, response.text
            await _wait_for_messages(
                client, yuubot_config, conversation_id, count=2
            )

            conflict = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_2,
                actor_id=second_actor["id"],
            )

        body = conflict.json()
        assert conflict.status_code == 409, body
        assert body["code"] == "conversation_binding_conflict"
        assert body["data"]["actor_id"] == first_actor["id"]
    finally:
        await daemon.stop()


async def test_transcript_endpoint_projects_from_history_items(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    """Red test 6.

    After a turn, ``GET /api/admin/conversations/{cid}/messages`` returns
    user + assistant rows projected from ``conversation_history_items``
    with the existing role-order assertions.
    """
    llm = ConversationProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    app = daemon.asgi_app()
    await daemon.start()
    try:
        async with _client(app, yuubot_config) as client:
            actor = await _provision_actor(
                client, yuubot_config, suffix="transcript"
            )
            conversation_id = "history-transcript-1"

            response = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_1,
                actor_id=actor["id"],
            )
            assert response.status_code == 202, response.text
            await _wait_for_messages(
                client, yuubot_config, conversation_id, count=2
            )

            second_response = await _send_message(
                client,
                yuubot_config,
                conversation_id,
                CONVERSATION_TEXT_2,
                actor_id=actor["id"],
            )
            assert second_response.status_code == 202, second_response.text
            messages = await _wait_for_messages(
                client, yuubot_config, conversation_id, count=4
            )

        assert [m["role"] for m in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ], [m["role"] for m in messages]
        assert CONVERSATION_TEXT_1 in messages[0]["raw_content"]
        assert AGENT_REPLY in messages[1]["raw_content"]
    finally:
        await daemon.stop()
