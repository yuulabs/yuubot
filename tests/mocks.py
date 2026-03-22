"""Mock fixtures for external APIs — the only things we mock."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from urllib.parse import urlparse
from unittest.mock import patch

import httpx
import respx
import yuullm


# ── Constants ────────────────────────────────────────────────────

HHSH_RESPONSE = [{"name": "yyds", "trans": ["永远的神"]}]

RECORDER_SEND_OK = {"status": "ok", "retcode": 0}

GROUP_LIST = [{"group_id": 1000, "group_name": "测试群"}]
LOGIN_INFO = {"nickname": "测试机器人"}

OPENAI_RESPONSE_TEXT = "Hello from mock LLM"


# ── Recorder API mock (respx) ───────────────────────────────────

def _default_base_url() -> str:
    return os.environ.get("YUUBOT_TEST_RECORDER_API", "http://127.0.0.1:8767")


@contextmanager
def mock_recorder_api(base_url: str | None = None):
    """Mock Recorder HTTP API. Yields list of captured send_msg request bodies."""
    base_url = base_url or _default_base_url()
    sent: list[dict] = []

    with respx.mock(assert_all_called=False) as router:
        def _capture_send(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=RECORDER_SEND_OK)

        router.post(f"{base_url}/send_msg").mock(side_effect=_capture_send)
        router.get(f"{base_url}/get_group_list").mock(
            return_value=httpx.Response(200, json={"data": GROUP_LIST}),
        )
        router.get(f"{base_url}/get_login_info").mock(
            return_value=httpx.Response(200, json={"data": LOGIN_INFO}),
        )
        yield sent


@contextmanager
def fake_recorder_api_server(host: str = "127.0.0.1", port: int | None = None):
    """Serve a minimal recorder_api over real HTTP for subprocess-based tests."""
    if port is None:
        parsed = urlparse(_default_base_url())
        port = parsed.port or 8767

    sent: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _reply(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/get_group_list":
                self._reply(200, {"data": GROUP_LIST})
                return
            if self.path == "/get_login_info":
                self._reply(200, {"data": LOGIN_INFO})
                return
            self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/send_msg":
                self._reply(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            sent.append(json.loads(body))
            self._reply(200, RECORDER_SEND_OK)

    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield sent
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ── hhsh API mock (respx) ───────────────────────────────────────

@contextmanager
def mock_hhsh_api():
    """Mock nbnhhsh API."""
    with respx.mock(assert_all_called=False) as router:
        router.post("https://lab.magiconch.com/api/nbnhhsh/guess").mock(
            return_value=httpx.Response(200, json=HHSH_RESPONSE),
        )
        yield


# ── LLM mock — patches provider.stream() ────────────────────────

def make_text_response(text: str) -> list:
    """Build a list of yuullm StreamItems for a plain text response."""
    return [yuullm.Response(item={"type": "text", "text": text})]


def make_tool_call_response(
    tool_name: str, arguments: str, call_id: str = "call_001",
) -> list:
    """Build StreamItems for a single tool call."""
    return [yuullm.ToolCall(id=call_id, name=tool_name, arguments=arguments)]


def make_tool_then_text(
    tool_name: str, arguments: str, text: str,
    call_id: str = "call_001",
) -> tuple[list, list]:
    """Return (tool_call_items, text_items) as two separate responses."""
    return (
        make_tool_call_response(tool_name, arguments, call_id),
        make_text_response(text),
    )


_FAKE_USAGE = yuullm.Usage(
    provider="test", model="test-model",
    input_tokens=10, output_tokens=10, total_tokens=20,
)


@contextmanager
def mock_llm(
    responses: list[list] | None = None,
    *,
    usages: list[yuullm.Usage] | None = None,
    costs: list[yuullm.Cost | None] | None = None,
):
    """Patch OpenAIChatCompletionProvider.stream to return fake stream items.

    *responses* is a list of item-lists. Each LLM call consumes one entry.
    If None, returns a single text response.
    """
    if responses is None:
        responses = [make_text_response(OPENAI_RESPONSE_TEXT)]

    call_idx = 0

    async def _fake_stream(self, messages, *, model, tools=None, **kw):
        nonlocal call_idx
        idx = min(call_idx, len(responses) - 1)
        call_idx += 1
        items = list(responses[idx])

        async def _iter():
            for item in items:
                yield item

        usage = _FAKE_USAGE if usages is None else usages[min(idx, len(usages) - 1)]
        cost = None if costs is None else costs[min(idx, len(costs) - 1)]
        store = yuullm.Store(usage=usage, cost=cost)
        return _iter(), store

    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider,
        "stream",
        _fake_stream,
    ):
        yield
