from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import pytest
from attrs import define, field
from openai.types.chat import ChatCompletionChunk
from yuubot.domain import (
    ContentItem,
    ConversationContext,
    GenReasoning,
    GenText,
    InputMessage,
    LLMInput,
    ModelCard,
    text_content,
)
from yuubot.llm.openai import (
    OpenAIProvider,
    OpenAIProviderConfig,
    ToolStreamState,
    _events_from_chunk,
    _messages,
    _presets_for_endpoint,
)
from yuubot.runtime.cache import CachePool


@define
class RecordingCompletions:
    calls: list[dict[str, object]] = field(factory=list)

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return object()


@define
class RecordingChat:
    completions: RecordingCompletions


@define
class RecordingClient:
    chat: RecordingChat


async def test_openai_provider_passes_actor_reasoning_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = await _completion_kwargs(
        tmp_path,
        monkeypatch,
        ModelCard("gpt-test", "high"),
        {"reasoning_effort": "medium"},
    )

    assert kwargs["model"] == "gpt-test"
    assert kwargs["reasoning_effort"] == "high"


async def test_openai_provider_omits_empty_reasoning_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = await _completion_kwargs(
        tmp_path,
        monkeypatch,
        ModelCard("gpt-test"),
    )

    assert kwargs["model"] == "gpt-test"
    assert "reasoning_effort" not in kwargs


def test_openai_provider_replays_reasoning_content(tmp_path: Path) -> None:
    messages = _messages(
        [
            InputMessage("user", "actor", text_content("hello")),
            GenReasoning("hidden chain"),
            GenText("visible answer"),
            InputMessage("user", "actor", text_content("continue")),
        ],
        tmp_path,
        CachePool(),
    )

    assert messages == [
        {"role": "user", "name": "actor", "content": "hello"},
        {"role": "assistant", "content": "visible answer", "reasoning_content": "hidden chain"},
        {"role": "user", "name": "actor", "content": "continue"},
    ]


def test_openai_provider_caches_image_data_url_with_explicit_size(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image-bytes")
    cache = CachePool()

    messages = _messages(
        [
            InputMessage(
                "user",
                "actor",
                [ContentItem("image", path="image.png", mime="image/png")],
            )
        ],
        tmp_path,
        cache,
    )

    expected_url = "data:image/png;base64,aW1hZ2UtYnl0ZXM="
    assert messages == [
        {
            "role": "user",
            "name": "actor",
            "content": [{"type": "image_url", "image_url": {"url": expected_url}}],
        }
    ]

    stat = image_path.stat()
    _, cached = cache.get(
        f"content-image-v1:{image_path}:{stat.st_mtime_ns}:{stat.st_size}:image/png"
    )
    assert cached.value == expected_url
    assert cached.get_cache_size() == len(expected_url)


def test_openai_provider_keeps_tool_argument_chunks_without_repeated_type() -> None:
    tool_state = ToolStreamState()
    first = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "execute_python", "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
    )
    second = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-2",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": '{"code": "print(1)"}'}}]
                    },
                    "finish_reason": None,
                }
            ],
        }
    )

    events = [*_events_from_chunk(first, tool_state), *_events_from_chunk(second, tool_state)]

    assert [event.kind for event in events] == ["tool_name", "tool_arguments_delta"]
    assert msgspec.to_builtins(events[1].payload) == {"text": '{"code": "print(1)"}'}
    assert tool_state.seen == {0}
    assert tool_state.named == {0}


def test_openai_provider_waits_for_tool_name_when_id_arrives_first() -> None:
    tool_state = ToolStreamState()
    first = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "id": "call-1", "type": "function"}]},
                    "finish_reason": None,
                }
            ],
        }
    )
    second = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-2",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"name": "execute_python"}}]},
                    "finish_reason": None,
                }
            ],
        }
    )

    events = [*_events_from_chunk(first, tool_state), *_events_from_chunk(second, tool_state)]

    assert [event.kind for event in events] == ["tool_name"]
    assert msgspec.to_builtins(events[0].payload) == {"id": "call-1", "name": "execute_python"}
    assert tool_state.seen == {0}
    assert tool_state.named == {0}


def test_openai_provider_buffers_tool_arguments_until_tool_name() -> None:
    tool_state = ToolStreamState()
    first = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"code": "'}}]},
                    "finish_reason": None,
                }
            ],
        }
    )
    second = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-2",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "execute_python", "arguments": "print(1)\"}"}}
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
    )

    events = [*_events_from_chunk(first, tool_state), *_events_from_chunk(second, tool_state)]

    assert [event.kind for event in events] == ["tool_name", "tool_arguments_delta", "tool_arguments_delta"]
    assert [msgspec.to_builtins(event.payload) for event in events] == [
        {"id": "tool-0", "name": "execute_python"},
        {"text": '{"code": "'},
        {"text": "print(1)\"}"},
    ]
    assert tool_state.pending_arguments == {}


def test_openai_provider_accepts_legacy_streaming_function_call() -> None:
    tool_state = ToolStreamState()
    chunk = ChatCompletionChunk.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "legacy-compatible",
            "choices": [
                {
                    "index": 0,
                    "delta": {"function_call": {"name": "execute_python", "arguments": '{"code": "print(1)"}'}},
                    "finish_reason": None,
                }
            ],
        }
    )

    events = _events_from_chunk(chunk, tool_state)

    assert [event.kind for event in events] == ["tool_name", "tool_arguments_delta"]
    assert msgspec.to_builtins(events[0].payload) == {"id": "tool-0", "name": "execute_python"}
    assert msgspec.to_builtins(events[1].payload) == {"text": '{"code": "print(1)"}'}
    assert tool_state.seen == {0}
    assert tool_state.named == {0}


async def test_list_presets_scoped_to_endpoint() -> None:
    openai = OpenAIProvider(OpenAIProviderConfig(api_key="test-key"))
    deepseek = OpenAIProvider(OpenAIProviderConfig(api_key="test-key", endpoint="https://api.deepseek.com"))
    custom = OpenAIProvider(OpenAIProviderConfig(api_key="test-key", endpoint="https://proxy.example/v1"))

    openai_selectors = {card.selector for card in await openai.list_presets()}
    deepseek_selectors = {card.selector for card in await deepseek.list_presets()}
    custom_selectors = {card.selector for card in await custom.list_presets()}

    assert openai_selectors == {"gpt-4o", "gpt-4o-mini"}
    assert deepseek_selectors == {"deepseek-chat", "deepseek-reasoner"}
    assert custom_selectors == set()


def test_presets_for_endpoint() -> None:
    assert {card.selector for card in _presets_for_endpoint("")} == {"gpt-4o", "gpt-4o-mini"}
    assert {card.selector for card in _presets_for_endpoint("https://api.deepseek.com")} == {
        "deepseek-chat",
        "deepseek-reasoner",
    }
    assert _presets_for_endpoint("https://proxy.example/v1") == ()


async def _completion_kwargs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: ModelCard,
    options: dict[str, object] | None = None,
) -> dict[str, object]:
    provider = OpenAIProvider(OpenAIProviderConfig(api_key="test-key", options=options or {}))
    completions = RecordingCompletions()
    client = RecordingClient(RecordingChat(completions))

    def sdk_client(self: OpenAIProvider) -> Any:
        del self
        return client

    monkeypatch.setattr(OpenAIProvider, "_sdk_client", sdk_client)
    await provider._completion_stream(
        LLMInput([], []),
        model,
        _context(tmp_path, model),
        CachePool(),
    )
    return completions.calls[0]


def _context(tmp_path: Path, model: ModelCard) -> ConversationContext:
    return ConversationContext(
        model,
        "conversation",
        "actor",
        tmp_path,
    )
