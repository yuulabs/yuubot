"""E2E tests for LLM flow — signal queuing, timeout, error steps, agent-fns via real daemon."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from unittest.mock import patch

import msgspec
import pytest
import yuullm
import yuuagents as ya
import yaml

from yuubot.core.onebot import to_inbound_message
from yuubot.core.models import Context, MessageRecord
from tests.framework import ActorTestRunner, ToolStep
from tests.conftest import (
    make_private_event,
)
from tests.framework import (
    ScriptedLLM,
    RecorderMock,
    text,
    execute_python,
    tool_call,
)
from tests.mocks import fake_recorder_api_server


# ── Signal queuing: image during running conversation ────────────

@pytest.mark.asyncio
@pytest.mark.skip(reason="Signal queuing not yet supported in Mate-based architecture; pending re-implementation")
async def test_image_signal_queued_during_running_conversation(db, yuubot_config) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    captured_messages: list[list[yuullm.Message]] = []
    call_count = 0

    async def _fake_stream(self, messages, *, model, tools=None, **kw):
        nonlocal call_count
        del self, model, tools, kw
        call_count += 1
        captured_messages.append(list(messages))
        if call_count == 1:
            started.set()
            await release.wait()
            tt = "没看到图"
        else:
            tt = "看到图了"

        async def _gen() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": tt})

        return _gen(), yuullm.Store(
            usage=yuullm.Usage(provider="test", model="test-model", input_tokens=1, output_tokens=1),
        )

    image_event = make_private_event("", ctx_id=300)
    image_event["message"] = [
        {"type": "image", "data": {
            "url": "https://example.invalid/queued.png",
            "file": "queued.png",
            "local_path": "/tmp/yuubot-queued-image.png",
        }},
    ]
    image_event["raw_message"] = "[CQ:image,file=queued.png]"

    runner = ActorTestRunner(config=yuubot_config)

    try:
        with RecorderMock() as recorder:
            with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _fake_stream):
                task = asyncio.create_task(
                    runner.run_direct_turn(
                        to_inbound_message(make_private_event("图呢？", ctx_id=300)),
                        agent_name="shiori",
                        bot_kind="master",
                    ),
                )
                await asyncio.wait_for(started.wait(), timeout=5)

                signal = await runner.render_signal(to_inbound_message(image_event))
                runner.enqueue_signal(list(runner._sessions_by_runtime.values())[0].agent.id, signal)

                release.set()
                await task

        assert call_count >= 2
        assert recorder.texts == ["没看到图", "看到图了"]
    finally:
        release.set()
        await runner.stop()


# ── Slow LLM ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_slow_llm_completes_without_inactivity_timeout(db, yuubot_config) -> None:

    async def _slow_stream(self, messages, *, model, tools=None, **kw):
        del self, messages, model, tools, kw
        # Simulate a very slow LLM that takes longer than timeout
        await asyncio.sleep(1.0)

        async def _gen() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": "too late"})

        return _gen(), yuullm.Store(
            usage=yuullm.Usage(provider="test", model="test-model", input_tokens=1, output_tokens=1),
        )

    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _slow_stream):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=301)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    assert session.status == "idle"
    assert session.final_text == "too late"
    await runner.stop()


# ── LLM error handling ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_stream_error_does_not_crash_runner(db, yuubot_config) -> None:
    async def _error_stream(self, messages, *, model, tools=None, **kw):
        del self, messages, model, tools, kw
        raise RuntimeError("simulated LLM API failure")

    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _error_stream):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=302)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    assert session.status == "error"
    assert "simulated LLM API failure" in session.final_text
    await runner.stop()


# ── execute_python with im.send_message via local recorder HTTP ──

@pytest.mark.asyncio
async def test_im_send_message_via_execute_python_local_recorder(
    db, yuubot_config, recorder_api_port,
) -> None:
    """Agent calls im.send_message() through execute_python → local recorder HTTP."""
    code = (
        "import im\n"
        "r = await im.send_message('hello from agent!', ctx_id=SESSION_STATE.ctx_id)\n"
        "r\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("message sent"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with fake_recorder_api_server(port=recorder_api_port) as sent:
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("send message", ctx_id=303)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "sent" in tool_steps[0].output_text.lower() or "hello from agent" in tool_steps[0].output_text.lower()
    assert any("hello from agent!" in seg.get("data", {}).get("text", "")
               for body in sent for seg in body.get("message", []))
    await runner.stop()


# ── execute_python with im.recent_messages via local DB ──────────

@pytest.mark.asyncio
async def test_im_recent_messages_via_execute_python_local_db(
    db, yuubot_config,
) -> None:
    """Agent calls im.recent_messages() through execute_python → local DB."""
    code = (
        "import im\n"
        "msgs = await im.recent_messages(limit=5, ctx_id=SESSION_STATE.ctx_id)\n"
        "f'got {len(msgs)} messages'\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("recent", ctx_id=304)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "got" in tool_steps[0].output_text.lower() or "messages" in tool_steps[0].output_text.lower()
    # Error-free execution
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


@pytest.mark.asyncio
async def test_im_message_records_queryset_via_execute_python_local_db(
    db, yuubot_config,
) -> None:
    """Agent gets a local QuerySet[MessageRecord] and chains ORM filters."""
    await Context.get_or_create(id=308, defaults={"type": "private", "target_id": 10001})
    await MessageRecord.create(
        message_id=9001,
        ctx_id=308,
        user_id=10001,
        nickname="tester",
        display_name="tester",
        content="needle message for local orm",
        raw_message='[{"type":"text","data":{"text":"needle message for local orm"}}]',
        timestamp=datetime.now(timezone.utc),
        media_files=[],
    )
    code = (
        "import im\n"
        "qs = await im.message_records(ctx_id=SESSION_STATE.ctx_id, limit=20)\n"
        "rows = await qs.filter(content__icontains='needle')\n"
        "f'orm rows {len(rows)}: {rows[0].content if rows else \"none\"}'\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("orm messages", ctx_id=308)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert "needle message for local orm" in tool_steps[0].output_text
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── execute_python with mem.save/recall via local DB ─────────────

@pytest.mark.asyncio
async def test_mem_save_and_recall_via_execute_python_local_db(
    db, yuubot_config,
) -> None:
    """Agent saves and recalls memory through execute_python → local DB."""
    await Context.get_or_create(id=305, defaults={"type": "private", "target_id": 10001})
    code_save = (
        "import mem\n"
        "r = await mem.save_memory('猫最喜欢晒太阳', tags=['cat'], scope='private')\n"
        "r\n"
    )
    code_recall = (
        "import mem\n"
        "results = await mem.recall_memory('猫', limit=5)\n"
        "f'found {len(results)} memories: {results[0][\"content\"] if results else \"none\"}'\n"
    )
    code_queryset = (
        "import mem\n"
        "qs = await mem.memories(ctx_id=SESSION_STATE.ctx_id, limit=20)\n"
        "rows = await qs.filter(content__icontains='猫')\n"
        "f'orm memories {len(rows)}: {rows[0].content if rows else \"none\"}'\n"
    )
    llm = ScriptedLLM([
        execute_python(code_save),
        execute_python(code_recall),
        execute_python(code_queryset),
        text("memory test done"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("save memory", ctx_id=305)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 3
    assert "猫最喜欢晒太阳" in tool_steps[1].output_text
    assert "猫最喜欢晒太阳" in tool_steps[2].output_text
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── execute_python with web.read_page locally ────────────────────

@pytest.mark.asyncio
async def test_web_read_page_via_execute_python_local_http(
    db, yuubot_config, daemon_api_port,
) -> None:
    """Agent calls web.read_page() locally against a real HTTP test server."""
    with _fake_page_server(daemon_api_port) as url:
        code = (
            "import web\n"
            f"r = await web.read_page({url!r}, page_size=1000)\n"
            "f'{r[\"title\"]}: {\"Local Web Body\" in r[\"text\"]}'\n"
        )
        llm = ScriptedLLM([
            execute_python(code),
            text("web done"),
        ])
        runner = ActorTestRunner(config=yuubot_config)

        with RecorderMock():
            with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
                session = await runner.run_direct_turn(
                    to_inbound_message(make_private_event("read page", ctx_id=309)),
                    agent_name="general",
                    bot_kind="master",
                )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert "Local Page: True" in tool_steps[0].output_text
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── execute_python with schedule service via local DB ────────────

@pytest.mark.asyncio
async def test_schedule_create_via_execute_python_local_db(
    db, yuubot_config,
) -> None:
    """Agent creates a schedule through the yuuagents schedule provider."""
    llm = ScriptedLLM([
        tool_call("create_cron", {"cron": "0 */2 * * *", "actions": ["agent:shiori:take a break"]}),
        text("schedule created"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
            session = await runner.run_direct_turn(
                to_inbound_message(make_private_event("create schedule", ctx_id=306)),
                agent_name="shiori",
                bot_kind="master",
            )

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "AgentCallError" not in tool_steps[0].output_text
    await runner.stop()


# ── execute_python with vision.describe_image via mocked LLM API ─

@pytest.mark.asyncio
async def test_vision_describe_image_sends_local_image_as_base64_to_llm(
    db, yuubot_config, tmp_path, monkeypatch,
) -> None:
    """Agent calls vision.describe_image(); external LLM API receives the local image as a data URL."""
    image_bytes = b"\x89PNG\r\n\x1a\nfake-yuubot-image-bytes"
    image_path = tmp_path / "sent-image.png"
    image_path.write_bytes(image_bytes)
    expected_url = f"data:image/png;base64,{base64.b64encode(image_bytes).decode()}"

    code = (
        "import vision\n"
        f"result = await vision.describe_image({str(image_path)!r})\n"
        "result\n"
    )
    llm = ScriptedLLM([
        execute_python(code),
        text("图片看过了"),
    ])
    runner = ActorTestRunner(config=yuubot_config)

    with _mock_openai_chat_api("这是一张测试图片") as (base_url, requests):
        raw = msgspec.to_builtins(yuubot_config)
        raw["llm_roles"]["vision"] = "test/test-model"
        raw["yuuagents"]["providers"]["test"]["base_url"] = f"{base_url}/v1"
        config_path = tmp_path / "vision-config.yaml"
        config_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        monkeypatch.setenv("YUUBOT_CONFIG", str(config_path))

        event = make_private_event("看看这张图", ctx_id=307)
        event["message"] = [
            {"type": "text", "data": {"text": "看看这张图"}},
            {
                "type": "image",
                "data": {
                    "file": "sent-image.png",
                    "local_path": str(image_path),
                },
            },
        ]
        event["raw_message"] = "[CQ:image,file=sent-image.png]"

        try:
            with RecorderMock():
                with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", llm.build_handler()):
                    session = await runner.run_direct_turn(
                        to_inbound_message(event),
                        agent_name="shiori",
                        bot_kind="master",
                    )
        finally:
            await runner.stop()

    assert session is not None
    tool_steps = [s for s in session.steps if isinstance(s, ToolStep)]
    assert len(tool_steps) >= 1
    assert "这是一张测试图片" in tool_steps[0].output_text
    assert session.final_text == "图片看过了"

    chat_requests = [body for path, body in requests if path == "/v1/chat/completions"]
    assert len(chat_requests) == 1
    vision_message = chat_requests[0]["messages"][0]
    assert vision_message["content"] == [
        {
            "type": "text",
            "text": (
                "请用中文描述图片内容。按顺序说明画面主体、动作/表情、构图色调、"
                "可见文字和整体氛围。不要使用标题或编号。"
            ),
        },
        {
            "type": "image_url",
            "image_url": {"url": expected_url},
        },
    ]


@contextmanager
def _mock_openai_chat_api(description: str):
    requests: list[tuple[str, dict]] = []

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/v1/models":
                self._json(200, {"object": "list", "data": [{"id": "test-model", "object": "model"}]})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) if length else b"{}")
            requests.append((self.path, body))
            if self.path != "/v1/chat/completions":
                self._json(404, {"error": "not found"})
                return
            chunks = [
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "test-model",
                    "choices": [{"index": 0, "delta": {"content": description}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "test-model",
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            ]
            payload = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            data = payload.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ── Actor session reset via /yclose ──────────────────────────────

@pytest.mark.asyncio
async def test_yclose_resets_actor_session(db, yuubot_config) -> None:
    from yuubot.commands.builtin import build_command_tree
    from yuubot.commands.entry import EntryManager
    from yuubot.daemon.dispatcher import Dispatcher

    runner = ActorTestRunner(config=yuubot_config)
    with RecorderMock():
        with patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", ScriptedLLM([text("active")]).build_handler()):
            await runner.run_direct_turn(
                to_inbound_message(make_private_event("hello", ctx_id=307)),
                agent_name="shiori",
                bot_kind="master",
            )
    assert runner.master_actor.agents

    root = build_command_tree(yuubot_config.bot.entries)
    deps = {
        "entry_mgr": EntryManager(),
        "root": root,
        "config": yuubot_config,
        "master_actor": runner.master_actor,
        "group_actor": runner.group_actor,
    }
    dispatcher = Dispatcher(
        config=yuubot_config,
        root=root,
        deps=deps,
    )

    try:
        with RecorderMock() as recorder:
            await dispatcher.dispatch(make_private_event("/yclose", ctx_id=307))

        assert not runner.master_actor.agents
        assert recorder.texts[-1] == "会话已重置 ✨"
    finally:
        await dispatcher.stop()
        await runner.stop()


@contextmanager
def _fake_page_server(port: int):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self) -> None:
            body = (
                b"<html><head><title>Local Page</title></head>"
                b"<body><main>Local Web Body</main></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/page"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
