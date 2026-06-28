from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import yuullm
from yuullm.providers import _image_cache
from yuullm.providers.anthropic import AnthropicMessagesProvider
from yuullm.providers.openai import OpenAIChatCompletionProvider
from yuullm.providers.openrouter import OpenRouterProvider
from yuullm.types import with_last_item_cache_control


class _EmptyOpenAIStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeOpenAICompletions:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _EmptyOpenAIStream:
        self.kwargs = kwargs
        return _EmptyOpenAIStream()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.completions = _FakeOpenAICompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeAnthropicStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self) -> SimpleNamespace:
        usage = SimpleNamespace(
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return SimpleNamespace(model="claude-test", usage=usage)


class _FakeAnthropicMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> _FakeAnthropicStream:
        self.kwargs = kwargs
        return _FakeAnthropicStream()


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = _FakeAnthropicMessages()


_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search.",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    }
]


async def test_openai_provider_sends_all_content_blocks_to_sdk() -> None:
    client = _FakeOpenAIClient()
    provider = OpenAIChatCompletionProvider(api_key="test")
    provider._client = client

    system_message = yuullm.Message(
        "system",
        [
            {"type": "text", "text": "sys-1"},
            {"type": "text", "text": "sys-2"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ],
        {"name": "system_name"},
    )
    assistant_message = yuullm.assistant(
        {"type": "text", "text": "answer-1"},
        {"type": "text", "text": "answer-2"},
        {
            "type": "tool_call",
            "id": "call_1",
            "name": "search",
            "arguments": '{"q":"x"}',
        },
    )
    messages = [
        yuullm.tools(_TOOL_SPECS),
        system_message,
        yuullm.user("hello", {"type": "text", "text": " world"}),
        assistant_message,
        yuullm.tool("call_1", [{"type": "text", "text": "done"}]),
    ]

    await provider.stream(messages, model="gpt-test")

    assert client.completions.kwargs is not None
    assert client.completions.kwargs["tools"] == _TOOL_SPECS
    assert client.completions.kwargs["messages"] == [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "sys-1"},
                {"type": "text", "text": "sys-2"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/a.png"},
                },
            ],
            "name": "system_name",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": " world"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer-1"},
                {"type": "text", "text": "answer-2"},
            ],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": [{"type": "text", "text": "done"}],
        },
    ]


async def test_openai_provider_converts_file_image_urls_at_provider_boundary(
    tmp_path,
) -> None:
    client = _FakeOpenAIClient()
    provider = OpenAIChatCompletionProvider(api_key="test")
    provider._client = client
    image_path = tmp_path / "pixel.png"
    image_path.write_bytes(b"image-bytes")

    await provider.stream(
        [
            yuullm.tool(
                "call_1",
                [
                    {"type": "text", "text": "image"},
                    {"type": "image_url", "image_url": {"url": image_path.as_uri()}},
                ],
            )
        ],
        model="gpt-test",
    )

    assert client.completions.kwargs is not None
    content = client.completions.kwargs["messages"][0]["content"]
    assert content[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"image-bytes").decode("ascii")
    )


def test_file_image_cache_is_lru_bounded(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_image_cache, "_MAX_CACHE_BYTES", 80)
    _image_cache._file_image_cache.clear()
    _image_cache._file_image_cache_bytes = 0
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"a" * 20)
    second.write_bytes(b"b" * 20)

    _image_cache.image_url_for_provider(first.as_uri())
    _image_cache.image_url_for_provider(second.as_uri())

    assert len(_image_cache._file_image_cache) == 1
    assert next(iter(_image_cache._file_image_cache))[0] == second.as_uri()


async def test_openrouter_provider_preserves_cache_control_in_sdk_payload() -> None:
    client = _FakeOpenAIClient()
    provider = OpenRouterProvider(api_key="test")
    provider._client = client
    message = with_last_item_cache_control(
        yuullm.user("cache me"), {"type": "ephemeral"}
    )

    await provider.stream([message], model="openrouter/test")

    assert client.completions.kwargs is not None
    assert client.completions.kwargs["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "cache me",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]


async def test_anthropic_provider_sends_system_blocks_to_sdk() -> None:
    client = _FakeAnthropicClient()
    provider = AnthropicMessagesProvider(api_key="test")
    provider._client = client
    messages = [
        yuullm.tools(_TOOL_SPECS),
        yuullm.Message(
            "system",
            [
                {"type": "text", "text": "sys-1"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/a.png"},
                },
            ],
        ),
        yuullm.user("hello", {"type": "text", "text": " world"}),
        yuullm.assistant(
            "using tool",
            {
                "type": "tool_call",
                "id": "call_1",
                "name": "search",
                "arguments": '{"q":"x"}',
            },
        ),
        yuullm.tool("call_1", [{"type": "text", "text": "done"}]),
    ]

    stream, _store = await provider.stream(messages, model="claude-test")
    assert [item async for item in stream] == []

    assert client.messages.kwargs is not None
    assert client.messages.kwargs["tools"] == [
        {
            "name": "search",
            "description": "Search.",
            "input_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        }
    ]
    assert client.messages.kwargs["system"] == [
        {"type": "text", "text": "sys-1"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
    ]
    assert client.messages.kwargs["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": " world"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "using tool"},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "search",
                    "input": {"q": "x"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": [{"type": "text", "text": "done"}],
                }
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Constructor regression: provider_options (timeout, max_retries)
# ---------------------------------------------------------------------------


def test_openai_provider_accepts_timeout_and_max_retries() -> None:
    """Smoke test: constructor must accept common client-level settings."""
    provider = OpenAIChatCompletionProvider(
        api_key="sk-test", timeout=30.0, max_retries=5
    )
    assert provider._provider_name == "openai"


def test_anthropic_provider_accepts_timeout_and_max_retries() -> None:
    """Smoke test: constructor must accept common client-level settings."""
    provider = AnthropicMessagesProvider(api_key="sk-test", timeout=30.0, max_retries=5)
    assert provider._provider_name == "anthropic"


def test_openrouter_provider_accepts_timeout_and_max_retries() -> None:
    """Smoke test: constructor must accept common client-level settings."""
    provider = OpenRouterProvider(api_key="sk-test", timeout=30.0, max_retries=5)
    assert provider._provider_name == "openrouter"
