"""OpenAI-compatible chat-completions provider."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import msgspec
from attrs import define, field
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam, ChatCompletionToolParam

from ..domain.messages import (
    ContentItem,
    ConversationContext,
    GenReasoning,
    GenText,
    GenToolCall,
    HistoryItem,
    InputMessage,
    LLMInput,
    ModelCard,
    ToolResult,
)
from ..domain.stream import (
    ReasoningDeltaPayload,
    StreamEvent,
    TextDeltaPayload,
    ToolArgumentsDeltaPayload,
    ToolNamePayload,
    Usage,
    estimate_cost,
)
from ..util.paths import safe_workspace_path
from ..util.stream import stream_stop_event
from ..runtime.cache import CachePool
from .catalog import merge_catalog
from .protocol import Provider
from .records import ProviderRecord
from .types import AccountSnapshot, ValidationResult


class OpenAIProviderConfig(msgspec.Struct, frozen=True):
    endpoint: str = ""
    api_key: str = ""
    options: dict[str, Any] = msgspec.field(default_factory=dict)


_OPENAI_PRESETS: tuple[ModelCard, ...] = (
    ModelCard("gpt-4o", vision=True, input_price_per_million=2.5, output_price_per_million=10.0),
    ModelCard("gpt-4o-mini", vision=True, input_price_per_million=0.15, output_price_per_million=0.6),
)


@define
class ToolStreamState:
    seen: set[int] = field(factory=set)
    named: set[int] = field(factory=set)
    ids: dict[int, str] = field(factory=dict)
    pending_arguments: dict[int, list[str]] = field(factory=dict)


@define(frozen=True)
class _CachedImageDataUrl:
    value: str

    def get_cache_size(self) -> int:
        return len(self.value)


_DEEPSEEK_PRESETS: tuple[ModelCard, ...] = (
    ModelCard("deepseek-chat", input_price_per_million=0.27, output_price_per_million=1.1),
    ModelCard("deepseek-reasoner", toolcall=False, input_price_per_million=0.55, output_price_per_million=2.19),
)


def make_openai_provider(record: ProviderRecord, config: OpenAIProviderConfig) -> Provider:
    del record
    return OpenAIProvider(config)


@define
class OpenAIProvider:
    config: OpenAIProviderConfig
    _client: AsyncOpenAI | None = field(default=None, init=False)

    async def list_presets(self) -> list[ModelCard]:
        return list(_presets_for_endpoint(self.config.endpoint))

    async def list_remote_models(self) -> list[str]:
        page = await self._sdk_client().models.list()
        return sorted(model.id for model in page.data)

    def merge_catalog(self, presets: list[ModelCard], remote: list[str]) -> list[ModelCard]:
        return merge_catalog(presets, remote)

    async def get_balance(self) -> AccountSnapshot | None:
        return None

    async def validate(self) -> ValidationResult:
        if not self.config.api_key:
            return ValidationResult(False, "api_key is required")
        try:
            await self.list_remote_models()
        except Exception as exc:
            return ValidationResult(False, str(exc), {"type": type(exc).__name__})
        return ValidationResult(True)

    async def stream(
        self,
        input: LLMInput,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        usage = Usage()
        if stop_event.is_set():
            yield stream_stop_event("interrupted", usage, {}, False)
            return
        upstream = await self._completion_stream(input, model, context, cache)
        tool_state = ToolStreamState()
        finish_reason = "stop"
        try:
            async for chunk in upstream:
                if stop_event.is_set():
                    yield stream_stop_event("interrupted", usage, {}, False)
                    return
                for event in _events_from_chunk(chunk, tool_state):
                    yield event
                if chunk.usage is not None:
                    usage = _usage_from_chunk(chunk.usage)
                for choice in chunk.choices:
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
        finally:
            await upstream.close()
        for index in sorted(tool_state.named):
            yield StreamEvent(f"tool-{index}", "tool_arguments_end")
        payg = usage.payg_cost
        cost_estimated = False
        if payg is None and _has_tokens(usage):
            payg = estimate_cost(model, usage)
            cost_estimated = True
        yield stream_stop_event(finish_reason, _usage_with_payg(usage, payg), {}, cost_estimated)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _completion_stream(
        self,
        input: LLMInput,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
    ) -> AsyncStream[ChatCompletionChunk]:
        options = {key: value for key, value in self.config.options.items() if key != "timeout_s"}
        options.setdefault("stream_options", {"include_usage": True})
        reasoning_effort = model.reasoning_effort.strip()
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        messages = _messages(input.messages, context.workspace, cache)
        if input.tool_specs:
            options["tools"] = cast(list[ChatCompletionToolParam], input.tool_specs)
        completion = await self._sdk_client().chat.completions.create(
            model=model.selector,
            messages=messages,
            stream=True,
            **options,
        )
        return cast(AsyncStream[ChatCompletionChunk], completion)

    def _sdk_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=_base_url(self.config.endpoint),
                timeout=_timeout(self.config),
            )
        return self._client


def _base_url(endpoint: str) -> str:
    if not endpoint:
        return "https://api.openai.com/v1"
    normalized = endpoint.rstrip("/")
    return normalized.removesuffix("/chat/completions")


def _presets_for_endpoint(endpoint: str) -> tuple[ModelCard, ...]:
    base = _base_url(endpoint).lower()
    if "deepseek" in base:
        return _DEEPSEEK_PRESETS
    if "openai.com" in base:
        return _OPENAI_PRESETS
    return ()


def _timeout(config: OpenAIProviderConfig) -> float:
    value = config.options.get("timeout_s")
    return float(value) if isinstance(value, (int, float, str)) else 120.0


def _usage_with_payg(usage: Usage, payg: float | None) -> Usage:
    return Usage(
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        payg,
    )


def _has_tokens(usage: Usage) -> bool:
    return usage.input_tokens > 0 or usage.cached_input_tokens > 0 or usage.output_tokens > 0


def _usage_from_chunk(usage: object) -> Usage:
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None) or 0
    return Usage(
        int(prompt_tokens),
        int(cached),
        int(completion_tokens),
        None,
    )


def _messages(history: list[HistoryItem], workspace: Path, cache: CachePool) -> list[ChatCompletionMessageParam]:
    messages: list[dict[str, object]] = []
    assistant: dict[str, object] | None = None

    def flush_assistant() -> None:
        nonlocal assistant
        if assistant is not None:
            messages.append(assistant)
            assistant = None

    for item in history:
        if isinstance(item, InputMessage):
            flush_assistant()
            content = _input_content(item.content, workspace, cache)
            if item.role == "developer":
                messages.append({"role": "system", "content": content})
            else:
                messages.append({"role": item.role, "name": item.name, "content": content})
        elif isinstance(item, GenReasoning):
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            existing = assistant.get("reasoning_content", "")
            assistant["reasoning_content"] = f"{existing}{item.text}"
        elif isinstance(item, GenText):
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            existing = assistant.get("content", "")
            assistant["content"] = f"{existing}{item.text}"
        elif isinstance(item, GenToolCall):
            if assistant is None:
                assistant = {"role": "assistant", "content": None, "tool_calls": []}
            elif "tool_calls" not in assistant:
                assistant["tool_calls"] = []
                assistant["content"] = assistant.get("content") or None
            tool_calls = assistant["tool_calls"]
            assert isinstance(tool_calls, list)
            tool_calls.append({"id": item.id, "type": "function", "function": {"name": item.name, "arguments": item.arguments}})
        elif isinstance(item, ToolResult):
            flush_assistant()
            messages.append({"role": "tool", "tool_call_id": item.tool_call_id, "content": _input_content(item.content, workspace, cache)})
    flush_assistant()
    return cast(list[ChatCompletionMessageParam], messages)


def _input_content(content: list[ContentItem], workspace: Path, cache: CachePool) -> str | list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    for item in content:
        if item.kind == "text" and item.text:
            parts.append({"type": "text", "text": item.text})
        elif item.kind == "image" and item.url:
            parts.append({"type": "image_url", "image_url": {"url": item.url}})
        elif item.kind == "image" and item.path:
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(item, workspace, cache)}})
    if not parts:
        return ""
    if len(parts) == 1 and parts[0]["type"] == "text":
        return cast(str, parts[0]["text"])
    return parts


def _image_data_url(item: ContentItem, workspace: Path, cache: CachePool) -> str:
    path = _content_path(item.path, workspace)
    stat = path.stat()
    mime = item.mime if item.mime.startswith("image/") else "image/*"
    key = f"content-image-v1:{path}:{stat.st_mtime_ns}:{stat.st_size}:{mime}"
    try:
        _, cached = cache.get(key)
        data_url = cast(_CachedImageDataUrl, cached)
    except KeyError:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = _CachedImageDataUrl(f"data:{mime};base64,{encoded}")
        cache.set(key, {"mime": mime}, data_url)
    return data_url.value


def _content_path(value: str, workspace: Path) -> Path:
    return safe_workspace_path(workspace, value, allow_absolute=True)


def _emit_tool_name(index: int, tool_id: str, name: str, state: ToolStreamState, events: list[StreamEvent]) -> None:
    if not name or index in state.named:
        return
    group_id = f"tool-{index}"
    if tool_id:
        state.ids.setdefault(index, tool_id)
    events.append(
        StreamEvent(
            group_id,
            "tool_name",
            ToolNamePayload(id=state.ids.get(index, group_id), name=name),
        )
    )
    state.named.add(index)
    for arguments in state.pending_arguments.pop(index, []):
        events.append(
            StreamEvent(
                group_id,
                "tool_arguments_delta",
                ToolArgumentsDeltaPayload(arguments),
            )
        )


def _emit_tool_arguments(index: int, arguments: str, state: ToolStreamState, events: list[StreamEvent]) -> None:
    if not arguments:
        return
    if index in state.named:
        events.append(
            StreamEvent(
                f"tool-{index}",
                "tool_arguments_delta",
                ToolArgumentsDeltaPayload(arguments),
            )
        )
    else:
        state.pending_arguments.setdefault(index, []).append(arguments)


def _events_from_chunk(chunk: ChatCompletionChunk, state: ToolStreamState) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    for choice in chunk.choices:
        delta = choice.delta
        if delta.content:
            events.append(
                StreamEvent("text-0", "text_delta", TextDeltaPayload(delta.content))
            )
        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            events.append(
                StreamEvent(
                    "reasoning-0",
                    "reasoning_delta",
                    ReasoningDeltaPayload(reasoning),
                )
            )
        if delta.function_call is not None:
            state.seen.add(0)
            state.ids.setdefault(0, "tool-0")
            _emit_tool_name(0, "tool-0", delta.function_call.name or "", state, events)
            _emit_tool_arguments(0, delta.function_call.arguments or "", state, events)
        for call in delta.tool_calls or []:
            if call.type is not None and call.type != "function":
                continue
            state.seen.add(call.index)
            if call.id:
                state.ids.setdefault(call.index, call.id)
            name = call.function.name if call.function and call.function.name else ""
            _emit_tool_name(call.index, call.id or f"tool-{call.index}", name, state, events)
            arguments = call.function.arguments if call.function and call.function.arguments else ""
            _emit_tool_arguments(call.index, arguments, state, events)
    return events
