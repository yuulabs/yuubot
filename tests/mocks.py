"""Mock fixtures for external APIs — the only things we mock."""

from contextlib import contextmanager
from unittest.mock import patch

import httpx
import respx
import yuullm


# ── Constants ────────────────────────────────────────────────────

HHSH_RESPONSE = [{"name": "yyds", "trans": ["永远的神"]}]

RECORDER_SEND_OK = {"status": "ok", "retcode": 0}

GROUP_LIST = [{"group_id": 1000, "group_name": "测试群"}]

OPENAI_RESPONSE_TEXT = "Hello from mock LLM"


# ── Recorder API mock (respx) ───────────────────────────────────

@contextmanager
def mock_recorder_api(base_url: str = "http://127.0.0.1:8767"):
    """Mock Recorder HTTP API. Yields list of captured send_msg request bodies."""
    sent: list[dict] = []

    with respx.mock(assert_all_called=False) as router:
        import json as _json

        def _capture_send(request: httpx.Request) -> httpx.Response:
            sent.append(_json.loads(request.content))
            return httpx.Response(200, json=RECORDER_SEND_OK)

        router.post(f"{base_url}/send_msg").mock(side_effect=_capture_send)
        router.get(f"{base_url}/get_group_list").mock(
            return_value=httpx.Response(200, json={"data": GROUP_LIST}),
        )
        yield sent


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
    return [yuullm.Response(item=text)]


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
def mock_llm(responses: list[list] | None = None):
    """Patch OpenAIChatCompletionProvider.stream to return fake stream items.

    *responses* is a list of item-lists. Each LLM call consumes one entry.
    If None, returns a single text response.
    """
    if responses is None:
        responses = [make_text_response(OPENAI_RESPONSE_TEXT)]

    call_idx = 0

    original_stream = yuullm.providers.OpenAIChatCompletionProvider.stream

    async def _fake_stream(self, messages, *, model, tools=None, **kw):
        nonlocal call_idx
        idx = min(call_idx, len(responses) - 1)
        call_idx += 1
        items = list(responses[idx])

        async def _iter():
            for item in items:
                yield item

        store = {"usage": _FAKE_USAGE, "cost": None}
        return _iter(), store

    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider,
        "stream",
        _fake_stream,
    ):
        yield
