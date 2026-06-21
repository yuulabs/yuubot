"""E2E coverage for Echo HTTP ingress through a real daemon."""

from __future__ import annotations

import json
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest
import yuullm

from tests.helpers import (
    insert_echo_actor_resources,
    register_test_llm_provider,
    make_test_daemon_infrastructure,
)
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.runtime.daemon import YuubotDaemon, build_daemon


SOURCE_PATH = "channels/http-echo"
ACTOR_ID = "echo-http-actor"
INTEGRATION_ID = "echo-http"
MESSAGE_ID = "http-msg-1"
SENDER_ID = "external-user"
ORIGINAL_TEXT = "hello from external integration"
REPLY_TEXT = "reply from actor"
REPLY_MESSAGE_ID = "reply-msg-1"


async def test_echo_http_ingress_round_trips_through_llm_and_echo_tool(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = EchoRoundTripProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
            system_prompt="You verify echo HTTP ingress.",
        )
        await daemon.resources.event_bus.drain()

        await daemon.actors.start_actor(resources.actor.id)
        assert daemon.actors.running_actor_ids() == [resources.actor.id]

        async with _client(daemon) as client:
            response = await client.post(
                "/integration/echo",
                json={
                    "integration_id": resources.integration.id,
                    "message_id": MESSAGE_ID,
                    "sender_id": SENDER_ID,
                    "sender_name": "External User",
                    "kind": "private",
                    "text": ORIGINAL_TEXT,
                    "content": [{"type": "text", "text": ORIGINAL_TEXT}],
                },
            )

        assert response.status_code == 202, response.json()
        assert response.json()["source"] == {
            "producer": "integration",
            "id": resources.integration.id,
            "path": SOURCE_PATH,
        }

        await _wait_for_llm_calls(llm, 2)
        assert len(llm.calls) == 2
        first_user_message = yuullm.render_message_text(llm.calls[0][-1])
        assert ORIGINAL_TEXT in first_user_message
        assert "External User" in first_user_message

        execute_python_description = _execute_python_description(llm.tools[0])
        assert "import yb" in execute_python_description
        assert "import tim" in execute_python_description
    finally:
        await daemon.stop()


async def test_echo_http_ingress_execute_python_has_builtin_surfaces(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = ReplyRoundTripProvider()
    register_test_llm_provider("openai", llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
            system_prompt="You reply through echo round-trip.",
        )
        await daemon.resources.event_bus.drain()
        await daemon.actors.start_actor(resources.actor.id)

        async with _client(daemon) as client:
            response = await client.post(
                "/integration/echo",
                json={
                    "integration_id": resources.integration.id,
                    "message_id": MESSAGE_ID,
                    "sender_id": SENDER_ID,
                    "sender_name": "External User",
                    "kind": "private",
                    "text": ORIGINAL_TEXT,
                    "content": [{"type": "text", "text": ORIGINAL_TEXT}],
                },
            )

        assert response.status_code == 202, response.json()

        await _wait_for_llm_calls(llm, 2)
        assert len(llm.calls) == 2
        first_user_message = yuullm.render_message_text(llm.calls[0][-1])
        assert ORIGINAL_TEXT in first_user_message
        assert "External User" in first_user_message

        execute_python_description = _execute_python_description(llm.tools[0])
        assert "import yb" in execute_python_description
        assert "import tim" in execute_python_description
    finally:
        await daemon.stop()


class EchoRoundTripProvider:
    def __init__(self) -> None:
        self.calls: list[list[yuullm.Message]] = []
        self.tools: list[list[dict[str, Any]]] = []

    @property
    def api_type(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "scripted"

    async def list_models(self) -> list[yuullm.ProviderModel]:
        return [yuullm.ProviderModel(id="gpt-4")]

    async def stream(
        self,
        history: yuullm.History,
        *,
        model: str,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        _ = model, on_raw_chunk, kwargs
        messages, tools = yuullm.split_history(history)
        self.calls.append(list(messages))
        self.tools.append(list(tools or ()))
        turn = self._tool_turn() if len(self.calls) == 1 else self._done_turn()

        async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
            for item in turn:
                yield item

        return stream_items(), yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model="fake",
                input_tokens=1,
                output_tokens=1,
            )
        )

    def _tool_turn(self) -> list[yuullm.StreamItem]:
        code = (
            "import yb\n"
            f"message = {ORIGINAL_TEXT!r}\n"
            "result = {\n"
            "    'value': f\"{ACTOR_ID}:{SESSION_ID.startswith(SESSION_STATE['actor_id'])}:{MAILBOX_ID}\",\n"
            "    'message': message,\n"
            f"    'sender_id': {SENDER_ID!r},\n"
            f"    'message_id': {MESSAGE_ID!r},\n"
            "}\n"
            "print(result)"
        )
        return [
            yuullm.ToolCall(
                id="call-echo",
                name="execute_python",
                arguments=json.dumps(
                    {
                        "code": code,
                        "capture": ["stdout", "stderr"],
                    }
                ),
            )
        ]

    def _done_turn(self) -> list[yuullm.StreamItem]:
        return [yuullm.Response({"type": "text", "text": "done"})]


class ReplyRoundTripProvider(EchoRoundTripProvider):
    def _tool_turn(self) -> list[yuullm.StreamItem]:
        code = (
            "import yb\n"
            "result = {\n"
            f"    'text': {REPLY_TEXT!r},\n"
            f"    'message': {ORIGINAL_TEXT!r},\n"
            f"    'sender_id': {SENDER_ID!r},\n"
            f"    'message_id': {REPLY_MESSAGE_ID!r},\n"
            f"    'in_reply_to_message_id': {MESSAGE_ID!r},\n"
            "}\n"
            "print(result)"
        )
        return [
            yuullm.ToolCall(
                id="call-reply",
                name="execute_python",
                arguments=json.dumps(
                    {
                        "code": code,
                        "capture": ["stdout", "stderr"],
                    }
                ),
            )
        ]


async def _build_daemon(
    base_config: BootstrapConfig,
    tmp_path: Path,
) -> YuubotDaemon:
    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(
                data_dir=str(tmp_path / "data"),
            ),
        ),
        components=make_test_daemon_infrastructure(),
    )


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://testserver",
    )


async def _wait_for_llm_calls(
    llm: EchoRoundTripProvider,
    count: int,
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(llm.calls) < count:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected {count} LLM calls, got {len(llm.calls)}")
        await asyncio.sleep(0.01)


def _execute_python_description(tools: list[dict[str, Any]]) -> str:
    for tool in tools:
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name") == "execute_python":
            description = function.get("description", "")
            return description if isinstance(description, str) else ""
    raise AssertionError("execute_python tool spec was not sent to the LLM")
