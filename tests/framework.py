"""E2E test framework — ScriptedLLM, response builders, test daemon server."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import yuullm

import respx

_KEY = "YUUBOT_TEST_RECORDER_API"


def recorder_api_base() -> str:
    return os.environ.get(_KEY, "http://127.0.0.1:8767")


# ── Scripted LLM ──────────────────────────────────────────────────

class ScriptedLLM:
    """Mock LLM provider that follows a script and captures all calls for assertion."""

    def __init__(
        self,
        responses: list[list[yuullm.StreamItem]] | None = None,
        *,
        usages: list[yuullm.Usage] | None = None,
        costs: list[yuullm.Cost | None] | None = None,
    ) -> None:
        self.responses: list[list[yuullm.StreamItem]] = (
            list(responses) if responses else [_text_items("Hello from mock LLM")]
        )
        self._usages = usages
        self._costs = costs
        self.call_index = 0
        self.calls: list[ScriptedLLMCall] = []

    def clear(self) -> None:
        self.call_index = 0
        self.calls.clear()

    # ── Convenience mutators ──

    def set_responses(self, responses: list[list[yuullm.StreamItem]]) -> None:
        self.responses = list(responses)

    def set_single_text(self, text: str) -> None:
        self.responses = [_text_items(text)]

    def set_tool_then_text(self, tool_name: str, args: dict, text: str) -> None:
        self.responses = [
            _tool_call_items(tool_name, args),
            _text_items(text),
        ]

    def set_tool_then_tool_then_text(
        self, tool1: tuple[str, dict], tool2: tuple[str, dict], text: str,
    ) -> None:
        self.responses = [
            _tool_call_items(tool1[0], tool1[1]),
            _tool_call_items(tool2[0], tool2[1]),
            _text_items(text),
        ]

    def set_text_then_tool_then_text(
        self, text1: str, tool: tuple[str, dict], text2: str,
    ) -> None:
        self.responses = [
            _text_items(text1),
            _tool_call_items(tool[0], tool[1]),
            _text_items(text2),
        ]

    # ── build_handler() for safe patching ──

    def build_handler(self):
        """Return a closure suitable for patch.object on OpenAIChatCompletionProvider.stream.

        Using a bound method (self.stream) with patch.object causes the method to
        be stored as a class attribute — when accessed via an instance, Python's
        descriptor protocol rebinds it, which corrupts the binding. A closure
        avoids this.
        """
        llm = self

        async def handler(
            _self: object,
            messages: list[yuullm.Message],
            *,
            model: str | None = None,
            tools: list[dict[str, Any]] | None = None,
            **kwargs: object,
        ) -> yuullm.StreamResult:
            del _self, kwargs
            llm.calls.append(ScriptedLLMCall(
                messages=[_serializable_msg(m) for m in messages],
                tools=list(tools or []),
                model=model or "",
            ))

            idx = min(llm.call_index, len(llm.responses) - 1)
            items = list(llm.responses[idx])
            llm.call_index += 1

            usage_idx = min(idx, len(llm._usages or []) - 1) if llm._usages else 0
            usage = (
                llm._usages[usage_idx]
                if llm._usages
                else yuullm.Usage(provider="test", model="test-model", input_tokens=1, output_tokens=1)
            )
            cost = (
                llm._costs[usage_idx]
                if llm._costs and usage_idx < len(llm._costs)
                else None
            )

            async def _iter() -> AsyncIterator[yuullm.StreamItem]:
                for item in items:
                    yield item

            return _iter(), yuullm.Store(usage=usage, cost=cost)

        return handler

    # ── Assertion helpers ──

    @property
    def system_prompt(self) -> str:
        """Concatenated text of all system-role messages from the FIRST LLM call."""
        return self._system_prompt_for_call(0)

    def _system_prompt_for_call(self, idx: int) -> str:
        if idx >= len(self.calls):
            return ""
        return "\n".join(
            _msg_text(m) for m in self.calls[idx].messages if m["role"] == "system"
        )

    @property
    def user_texts(self) -> list[str]:
        """All user-role text from the first LLM call."""
        if not self.calls:
            return []
        return [_msg_text(m) for m in self.calls[0].messages if m["role"] == "user"]

    @property
    def tool_names(self) -> set[str]:
        """Names of tools received in the first LLM call."""
        if not self.calls:
            return set()
        tools = self.calls[0].tools
        return {t.get("function", {}).get("name", "") for t in tools if isinstance(t, dict)}

    @property
    def tool_descriptions_text(self) -> str:
        """All tool descriptions concatenated, for substring assertions."""
        if not self.calls:
            return ""
        parts: list[str] = []
        for t in self.calls[0].tools:
            fn = t.get("function", {}) if isinstance(t, dict) else {}
            parts.append(fn.get("description", ""))
        return "\n".join(parts)

    def call_messages(self, idx: int) -> list[dict]:
        """Raw messages dicts for the idx-th LLM call."""
        if idx >= len(self.calls):
            return []
        return self.calls[idx].messages


class ScriptedLLMCall:
    __slots__ = ("messages", "tools", "model")

    def __init__(self, messages: list[dict], tools: list[dict], model: str) -> None:
        self.messages = messages
        self.tools = tools
        self.model = model


def _serializable_msg(msg: yuullm.Message) -> dict:
    content = msg.content
    if isinstance(content, list):
        c = []
        for item in content:
            if yuullm.is_text_item(item):
                c.append({"type": "text", "text": item["text"]})
            elif yuullm.is_tool_call_item(item):
                c.append({"type": "tool_call", "name": item["name"]})
            elif yuullm.is_tool_result_item(item):
                c.append({"type": "tool_result"})
            elif yuullm.is_thinking_item(item):
                c.append({"type": "thinking"})
            elif yuullm.is_image_item(item):
                c.append({"type": "image_url"})
            else:
                c.append({"type": "unknown"})
    else:
        c = [{"type": "text", "text": str(content)}]
    return {"role": msg.role, "content": c}


def _msg_text(msg: dict) -> str:
    parts = []
    for item in msg.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


# ── Response builders ─────────────────────────────────────────────

def _text_items(text: str) -> list[yuullm.StreamItem]:
    return [yuullm.Response({"type": "text", "text": text})]


def _tool_call_items(name: str, args: dict, call_id: str = "call_001") -> list[yuullm.StreamItem]:
    return [yuullm.ToolCall(id=call_id, name=name, arguments=json.dumps(args, ensure_ascii=False))]


# Convenience aliases for test files
def text(text: str) -> list[yuullm.StreamItem]:
    return _text_items(text)


def tool_call(name: str, args: dict | str, call_id: str = "call_001") -> list[yuullm.StreamItem]:
    if isinstance(args, str):
        return [yuullm.ToolCall(id=call_id, name=name, arguments=args)]
    return _tool_call_items(name, args, call_id)


def execute_python(code: str) -> list[yuullm.StreamItem]:
    return tool_call("execute_python", {"code": code})


# ── Test daemon server ────────────────────────────────────────────

def _create_test_daemon_app(config) -> Any:
    from contextlib import asynccontextmanager
    from fastapi import FastAPI, Request
    from yuubot.daemon.local_api import create_agent_fn_router
    from yuubot.core.db import _load_simple_ext
    from tortoise import connections

    async def _ensure_simple_tokenizer():
        try:
            conn = connections.get("default")
            await conn.execute_query("SELECT 1")
            await _load_simple_ext(conn)
        except Exception:
            pass

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await _ensure_simple_tokenizer()
        yield

    app = FastAPI(lifespan=_lifespan)
    router = create_agent_fn_router(config=config)

    @app.middleware("http")
    async def _load_ext_middleware(request: Request, call_next):
        await _ensure_simple_tokenizer()
        return await call_next(request)

    app.include_router(router)
    return app


class TestDaemonServer:
    """Real FastAPI server for /agent-fns endpoints, runs in a background thread."""

    def __init__(self, config) -> None:
        self.config = config
        self.port: int = 0
        self._thread: threading.Thread | None = None
        self._server: Any = None

    def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        import uvicorn

        app = _create_test_daemon_app(self.config)
        uvicorn_config = uvicorn.Config(app, host=host, port=port, log_level="error")
        self._server = uvicorn.Server(uvicorn_config)

        def _run() -> None:
            self._server.run()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

        while not self._server.started:
            time.sleep(0.01)

        self.port = list(self._server.servers)[0].sockets[0].getsockname()[1]
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None


# ── Recorder API mock ─────────────────────────────────────────────

class RecorderMock:
    """Captures calls to the recorder HTTP API."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or recorder_api_base()
        self.sent: list[dict] = []

    def __enter__(self) -> RecorderMock:
        self._router = respx.mock(assert_all_called=False)
        self._router.__enter__()

        def _capture_send(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(200, json={"status": "ok", "retcode": 0})

        def _capture_guaranteed(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(200, json={"queued": True, "group_id": body.get("group_id", 0), "queue_size": 1})

        self._router.post(f"{self.base_url}/send_msg").mock(side_effect=_capture_send)
        self._router.post(f"{self.base_url}/send_msg_guaranteed").mock(side_effect=_capture_guaranteed)
        self._router.get(f"{self.base_url}/get_group_list").mock(
            return_value=httpx.Response(200, json={"data": [{"group_id": 1000, "group_name": "test"}]}),
        )
        self._router.get(f"{self.base_url}/get_login_info").mock(
            return_value=httpx.Response(200, json={"data": {"nickname": "testbot"}}),
        )
        return self

    def __exit__(self, *args: object) -> None:
        self._router.__exit__(*args)

    @property
    def texts(self) -> list[str]:
        """Extract text segments from captured send bodies."""
        result: list[str] = []
        for body in self.sent:
            for seg in body.get("message", []):
                if seg.get("type") == "text":
                    result.append(seg.get("data", {}).get("text", ""))
        return result


# ── E2E run helpers ───────────────────────────────────────────────

class ToolStep:
    __slots__ = ("name", "args", "output")

    def __init__(self, name: str, args: dict, output: str = "") -> None:
        self.name = name
        self.args = args
        self.output = output


async def _drive_agent_direct(
    runner,
    msg: dict,
    agent_name: str,
    bot_kind: str,
    llm: ScriptedLLM,
) -> tuple[str | None, list[ToolStep]]:
    """Drive an agent with scripted LLM, returning (final_text, tool_steps)."""
    from unittest.mock import patch
    import yuullm as yl
    from yuubot.core.onebot import to_inbound_message
    import yuuagents as ya

    tool_steps: list[ToolStep] = []

    inbound = to_inbound_message(msg)
    llm.clear()

    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler(),
    ):
        session = await runner.run_conversation(inbound, agent_name=agent_name, bot_kind=bot_kind)

    if session is None:
        return None, []

    for step in session.steps:
        if isinstance(step, ya.ToolStep):
            tool_steps.append(ToolStep(
                name=step.tool_name,
                args={},
                output=step.output_text or "",
            ))

    return session.final_text, tool_steps
