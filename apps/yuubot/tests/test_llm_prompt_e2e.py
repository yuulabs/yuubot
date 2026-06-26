"""E2E tests verifying LLM-facing prompt content during daemon round-trips.

The testing philosophy: we mock the LLM (scripted provider) to capture exactly what
the LLM receives — system prompt, tool specs, user messages — and verify they contain
the expected information so that a real LLM would make the right decisions.
"""

from __future__ import annotations

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

SOURCE_PATH = "channels/prompt-verify"
ACTOR_ID = "prompt-verify-actor"
INTEGRATION_ID = "prompt-verify"
SYSTEM_PROMPT = "You are a verification assistant that uses echo tool."


class PromptCapture:
    """Captures all LLM calls during a test for later assertion."""

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

        async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": "ok"})

        return stream_items(), yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model="fake",
                input_tokens=1,
                output_tokens=1,
            )
        )


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
    capture: PromptCapture,
    count: int,
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(capture.calls) < count:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"expected {count} LLM calls, got {len(capture.calls)}"
            )
        await asyncio.sleep(0.01)


def _system_text(calls: list[list[yuullm.Message]]) -> str:
    """Concatenate all system-role text from the first LLM call."""
    if not calls:
        return ""
    for msg in calls[0]:
        if msg.role == "system":
            content = msg.content if isinstance(msg.content, list) else []
            return "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    return ""


def _user_text(calls: list[list[yuullm.Message]]) -> str:
    """Concatenate all user-role text from the first LLM call."""
    if not calls:
        return ""
    for msg in calls[0]:
        if msg.role == "user":
            content = msg.content if isinstance(msg.content, list) else []
            return "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    return ""


def _tool_functions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract function definitions from tool specs."""
    functions = []
    for tool in tools:
        func = tool.get("function")
        if isinstance(func, dict):
            functions.append(func)
    return functions


def _find_tool_function(
    functions: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    for func in functions:
        if func.get("name") == name:
            return func
    return None


# --- Tests ---


async def test_system_prompt_includes_persona_and_runtime_guidance(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM sees a system prompt containing the actor persona,
    mode-specific guidance, and tool usage patterns."""
    capture = PromptCapture()
    register_test_llm_provider("openai", capture)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
            system_prompt=SYSTEM_PROMPT,
        )
        await daemon.resources.event_bus.drain()
        await daemon.actors.start_actor(ACTOR_ID)

        async with _client(daemon) as client:
            await client.post(
                "/integration/echo",
                json={
                    "integration_id": INTEGRATION_ID,
                    "message_id": "msg-ver-1",
                    "sender_id": "user-ver",
                    "sender_name": "Verifier",
                    "kind": "private",
                    "text": "verify prompt",
                    "content": [{"type": "text", "text": "verify prompt"}],
                },
            )

        await _wait_for_llm_calls(capture, 1)
        system = _system_text(capture.calls)
        assert SYSTEM_PROMPT in system, (
            f"System prompt should contain actor persona instructions.\n"
            f"Expected: {SYSTEM_PROMPT!r}\nGot: {system}"
        )
        assert "tim.Channel" in system, (
            "IM mode should include tim.Channel guidance in system prompt."
        )
    finally:
        await daemon.stop()


async def test_tool_spec_includes_execute_python_with_capability_imports(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM receives an execute_python tool spec that documents the
    available capability modules and their imports."""
    capture = PromptCapture()
    register_test_llm_provider("openai", capture)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
            system_prompt=SYSTEM_PROMPT,
        )
        await daemon.resources.event_bus.drain()
        await daemon.actors.start_actor(ACTOR_ID)

        async with _client(daemon) as client:
            await client.post(
                "/integration/echo",
                json={
                    "integration_id": INTEGRATION_ID,
                    "message_id": "msg-tool-1",
                    "sender_id": "user-tool",
                    "sender_name": "ToolTester",
                    "kind": "private",
                    "text": "test tools",
                    "content": [{"type": "text", "text": "test tools"}],
                },
            )

        await _wait_for_llm_calls(capture, 1)
        assert capture.tools, "LLM should receive tool specs."
        functions = _tool_functions(capture.tools[0])
        execute_python = _find_tool_function(functions, "execute_python")
        assert execute_python is not None, (
            "execute_python tool must be defined in LLM tool spec."
        )
        description = execute_python.get("description", "")

        assert "import yb" in description
        assert "import tim" in description
    finally:
        await daemon.stop()


async def test_llm_user_message_includes_source_and_sender_context(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM user message includes not just the text, but also who sent
    it and through which integration."""
    capture = PromptCapture()
    register_test_llm_provider("openai", capture)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
            system_prompt=SYSTEM_PROMPT,
        )
        await daemon.resources.event_bus.drain()
        await daemon.actors.start_actor(ACTOR_ID)

        async with _client(daemon) as client:
            await client.post(
                "/integration/echo",
                json={
                    "integration_id": INTEGRATION_ID,
                    "message_id": "msg-user-1",
                    "sender_id": "alice",
                    "sender_name": "Alice",
                    "kind": "private",
                    "text": "hello from alice",
                    "content": [{"type": "text", "text": "hello from alice"}],
                },
            )

        await _wait_for_llm_calls(capture, 1)
        user = _user_text(capture.calls)
        assert "hello from alice" in user, (
            "User message text should be present."
        )
        assert "Alice" in user, (
            "Sender name should be present in the user message."
        )
    finally:
        await daemon.stop()
