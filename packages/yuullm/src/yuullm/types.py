"""Core types for yuullm.

Content blocks remain plain ``TypedDict`` values aligned to provider APIs.
Messages are a single ``msgspec.Struct`` with role, content, and optional
provider-specific metadata.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Literal, Required, TypeGuard, TypedDict, cast, overload

import msgspec


# ---------------------------------------------------------------------------
# Core content types -- TypedDict definitions aligned with OpenAI API
# ---------------------------------------------------------------------------


class CacheControl(TypedDict, total=False):
    """Provider-specific prompt-cache metadata attached to a content block."""

    type: Required[Literal["ephemeral"]]
    ttl: int


class _CacheAnnotated(TypedDict, total=False):
    cache_control: CacheControl


class ToolCallItem(_CacheAnnotated):
    """A tool invocation embedded in an assistant message."""

    type: Literal["tool_call"]
    id: str
    name: str
    arguments: str  # raw JSON string


class TextItem(_CacheAnnotated):
    """A text content block."""

    type: Literal["text"]
    text: str


class _ImageURL(TypedDict, total=False):
    url: Required[str]
    detail: Literal["auto", "low", "high"]


class ImageItem(_CacheAnnotated):
    """An image content block (URL or base64)."""

    type: Literal["image_url"]
    image_url: _ImageURL


class _InputAudio(TypedDict, total=False):
    data: Required[str]  # base64 encoded
    format: Required[Literal["wav", "mp3"]]


class AudioItem(_CacheAnnotated):
    """An audio input content block."""

    type: Literal["input_audio"]
    input_audio: _InputAudio


class _FileData(TypedDict, total=False):
    file_data: str  # base64 encoded
    file_id: str
    filename: str


class FileItem(_CacheAnnotated):
    """A file content block."""

    type: Literal["file"]
    file: _FileData


ContentItem = TextItem | ImageItem | AudioItem | FileItem
Content = list[ContentItem]
ToolResultContent = str | Content


class ToolResultItem(_CacheAnnotated):
    """A tool execution result embedded in a tool message."""

    type: Literal["tool_result"]
    tool_call_id: str
    content: ToolResultContent


class ThinkingItem(TypedDict, total=False):
    """A thinking (chain-of-thought) block in an assistant message.

    Used to round-trip extended thinking content in multi-turn conversations.
    ``signature`` is required by Anthropic; absent for DeepSeek.
    """

    type: Required[Literal["thinking"]]
    thinking: Required[str]
    signature: str
    cache_control: CacheControl


class RedactedThinkingItem(TypedDict, total=False):
    """A redacted thinking block returned by Anthropic when thinking is hidden.

    Must be passed back as-is in subsequent turns.
    """

    type: Required[Literal["redacted_thinking"]]
    data: Required[str]
    cache_control: CacheControl


ProtocolItem = ToolCallItem | ToolResultItem | ThinkingItem | RedactedThinkingItem
MessageItem = ContentItem | ProtocolItem
MessageContent = list[MessageItem]
ToolOutput = str | ContentItem | Content
ToolArguments = dict[str, Any]


# ---------------------------------------------------------------------------
# StreamItem variants (output types -- what the model produces)
# ---------------------------------------------------------------------------


class Reasoning(msgspec.Struct, frozen=True):
    """A fragment of the model's chain-of-thought / extended thinking.

    The content is always a content item, typically a ``TextItem``.
    """

    item: ContentItem


class ToolCall(msgspec.Struct, frozen=True):
    """A tool invocation request emitted by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string

    def arguments_dict(self) -> ToolArguments:
        """Decode tool arguments as a JSON object."""
        return parse_tool_arguments(self.arguments, tool_name=self.name)

    @property
    def parsed_arguments(self) -> ToolArguments:
        """Alias for :meth:`arguments_dict`."""
        return self.arguments_dict()


class Response(msgspec.Struct, frozen=True):
    """A fragment of the model's final reply.

    The content is a provider-normalized content item.
    """

    item: ContentItem


class PartialToolCall(msgspec.Struct, frozen=True):
    """Partial tool-call metadata emitted before the full ``ToolCall`` lands.

    Used by providers to surface the tool *name* to consumers as soon as the
    provider knows it, while the JSON arguments are still streaming. Without
    this signal, downstream consumers (the agent loop, the SSE projector, the
    frontend) see nothing until the complete ``ToolCall`` arrives — for tools
    with large arguments (e.g. ``edit`` with long ``old_string``/``new_string``)
    that gap is multiple seconds of silence.

    On the cheapest cut only ``id`` and ``name`` are carried; partial JSON
    argument deltas stay unstreamed (the per-arg-delta version is deferred).
    """

    name: str
    id: str = ""


class Tick(msgspec.Struct, frozen=True):
    """Lightweight heartbeat yielded during tool-call argument streaming.

    While the provider accumulates tool-call deltas it normally yields
    nothing, which starves consumers that rely on the iteration loop to
    flush side-channel data (e.g. ``pending_sse`` populated by
    ``on_raw_chunk`` hooks).  ``Tick`` keeps the async-for loop spinning
    so those consumers can act promptly.

    ``partial_tool_call`` is set once per tool call — when the provider
    has resolved its name (and ``id`` if available) but the arguments
    are still streaming. Consumers (e.g. ``Agent.step``) MAY write a
    partial tool call to their EntityLog when this is set so the
    frontend can render a pending-state banner ("Editing...",
    "Running bash...") before the full ``ToolCall`` lands.

    Consumers that only care about ``Reasoning | ToolCall | Response``
    can safely ignore ``Tick`` when ``partial_tool_call is None``.
    """

    partial_tool_call: PartialToolCall | None = None


class ThinkingBlock(msgspec.Struct, frozen=True):
    """Complete thinking block emitted once per block when streaming finishes.

    Call ``to_message_item()`` to get a :class:`ThinkingItem` or
    :class:`RedactedThinkingItem` suitable for round-tripping in the next
    turn's assistant message.

    Anthropic: ``signature`` is set and required for round-trip.
    DeepSeek: ``signature`` is ``None``; ``reasoning_content`` is reconstructed
    from ``thinking`` by the OpenAI message converter.
    Redacted blocks (Anthropic): ``redacted_data`` is set; ``thinking`` is empty.
    """

    thinking: str
    signature: str | None = None
    redacted_data: str | None = None

    def to_message_item(self) -> ThinkingItem | RedactedThinkingItem:
        """Convert to a message item for use in the next turn's assistant message."""
        if self.redacted_data is not None:
            return {"type": "redacted_thinking", "data": self.redacted_data}
        item: ThinkingItem = {"type": "thinking", "thinking": self.thinking}
        if self.signature is not None:
            item["signature"] = self.signature
        return item


class StreamCursor(msgspec.Struct, frozen=True):
    """Position in the caller-visible stream and committed history.

    ``history_len`` points to the replayable committed history prefix.
    ``stream_seq`` points to the caller-visible stream item sequence.
    """

    history_len: int
    stream_seq: int


class AttemptRecovery(msgspec.Struct, frozen=True):
    """Control event emitted when a failed provider attempt is recovered.

    ``rollback_to`` tells callers which previously emitted stream position is
    still valid. ``continuation`` describes whether the next attempt can
    continue seamlessly from that point.
    """

    failed_provider: str
    failed_model: str
    next_provider: str | None
    next_model: str | None
    rollback_to: StreamCursor
    continuation: Literal["seamless", "non_seamless"]
    reason: str


StreamItem = Reasoning | ToolCall | Response | Tick | ThinkingBlock | AttemptRecovery


class ProviderModel(msgspec.Struct, frozen=True):
    """A model surfaced by a provider's model-list API."""

    id: str
    display_name: str | None = None
    supports_vision: bool | None = None


Role = Literal["system", "user", "assistant", "tool"]


class Message(msgspec.Struct, frozen=True):
    """A provider-agnostic chat message.

    ``provider_extra`` is copied through provider conversion so callers can
    pass vendor-specific message options without expanding yuullm's core model.
    """

    role: Role
    content: MessageContent
    provider_extra: dict[str, Any] = msgspec.field(default_factory=dict)


class ToolSpecs(msgspec.Struct, frozen=True):
    """Provider-neutral function tool schemas visible to the model."""

    tools: list[dict[str, Any]]


PromptItem = Message | ToolSpecs
History = list[PromptItem]


def split_history(
    history: Sequence[PromptItem],
) -> tuple[list[Message], list[dict[str, Any]] | None]:
    """Split logical history into provider wire inputs.

    Tool specs are a prompt item so copying history preserves the model-visible
    prefix. Providers still lower them into their own top-level tool fields.
    """
    messages: list[Message] = []
    tool_specs: list[dict[str, Any]] | None = None
    for item in history:
        if isinstance(item, Message):
            messages.append(item)
        elif isinstance(item, ToolSpecs):
            if tool_specs is not None:
                raise ValueError("history may contain at most one ToolSpecs item")
            tool_specs = list(item.tools)
        else:
            raise TypeError(f"unsupported history item: {type(item).__name__}")
    return messages, tool_specs


# ---------------------------------------------------------------------------
# Helper functions for constructing messages
# ---------------------------------------------------------------------------


def text(content: str) -> TextItem:
    """Create a text content item."""
    return {"type": "text", "text": content}


def tools(tool_specs: list[dict[str, Any]]) -> ToolSpecs:
    """Create a model-visible tool spec history item."""
    return ToolSpecs(list(tool_specs))


@overload
def _to_message_item(it: str) -> TextItem: ...


@overload
def _to_message_item(it: MessageItem) -> MessageItem: ...


def _to_message_item(it: str | MessageItem) -> MessageItem:
    """Convert a str to TextItem; pass dicts through."""
    if isinstance(it, str):
        return text(it)
    return it


def _to_content_item(it: str | ContentItem) -> ContentItem:
    item = _to_message_item(it)
    if (
        is_text_item(item)
        or is_image_item(item)
        or is_audio_item(item)
        or is_file_item(item)
    ):
        return item
    raise TypeError("user() only accepts text, image, audio, and file items")


def _to_assistant_item(
    it: str | TextItem | ToolCallItem | ThinkingItem | RedactedThinkingItem,
) -> TextItem | ToolCallItem | ThinkingItem | RedactedThinkingItem:
    item = _to_message_item(it)
    if (
        is_text_item(item)
        or is_tool_call_item(item)
        or is_thinking_item(item)
        or is_redacted_thinking_item(item)
    ):
        return item  # type: ignore[return-value]
    raise TypeError("assistant() only accepts text, tool-call, and thinking items")


def system(content: str, **provider_extra: Any) -> Message:
    """Create a system message."""
    return Message("system", [text(content)], provider_extra)


def user(*items: str | ContentItem, **provider_extra: Any) -> Message:
    """Create a user message with one or more content items.

    Examples::

        user("Hello!")
        user("What is this?", ImageItem(type="image_url", image_url={"url": "..."}))
    """
    return Message("user", [_to_content_item(it) for it in items], provider_extra)


def assistant(
    *items: str | TextItem | ToolCallItem | ThinkingItem | RedactedThinkingItem,
    **provider_extra: Any,
) -> Message:
    """Create an assistant message.

    Examples::

        assistant("Here is the answer.")
        assistant("Let me search.", ToolCallItem(
            type="tool_call",
            id="tc_1",
            name="search",
            arguments='{"q": "test"}',
        ))
    """
    return Message(
        "assistant", [_to_assistant_item(it) for it in items], provider_extra
    )


def coerce_tool_output_item(value: Any) -> ContentItem:
    """Validate and normalize a single tool output content item."""
    if not isinstance(value, dict):
        raise TypeError(
            f"tool content item must be a dict, got {type(value).__name__!r}"
        )
    item = value
    item_type = item.get("type")
    if item_type in {"text", "image_url", "input_audio", "file"}:
        return cast(ContentItem, item)
    raise TypeError(f"tool content item must be a content block, got {item_type!r}")


def coerce_tool_output(content: ToolOutput) -> ToolResultContent:
    """Normalize tool output into the canonical tool-result content shape."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [coerce_tool_output_item(item) for item in content]
    return [coerce_tool_output_item(content)]


def tool_result(tool_call_id: str, content: ToolOutput) -> ToolResultItem:
    """Create a tool-result item from string or structured tool output."""
    return {
        "type": "tool_result",
        "tool_call_id": tool_call_id,
        "content": coerce_tool_output(content),
    }


def tool(tool_call_id: str, content: ToolOutput, **provider_extra: Any) -> Message:
    """Create a tool result message.

    Content can be a plain string or a list of content blocks (for multimodal
    tool results, e.g. images). The list format follows OpenAI's convention::

        [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}]

    Example::

        tool("tc_1", "Search returned 5 results.")
        tool("tc_1", [{"type": "text", "text": "Here is the image"}, {"type": "image_url", ...}])
    """
    return Message("tool", [tool_result(tool_call_id, content)], provider_extra)


def parse_tool_arguments(
    arguments: str, *, tool_name: str | None = None
) -> ToolArguments:
    """Decode tool arguments as a JSON object."""
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        prefix = (
            f"Invalid tool arguments JSON for {tool_name}: "
            if tool_name is not None
            else "Invalid tool arguments JSON: "
        )
        raise ValueError(f"{prefix}{exc}. Arguments may have been truncated.") from exc
    if not isinstance(parsed, dict):
        prefix = (
            f"Invalid tool arguments JSON for {tool_name}: "
            if tool_name is not None
            else "Invalid tool arguments JSON: "
        )
        raise ValueError(f"{prefix}decoded arguments must be a JSON object")
    return parsed


@overload
def tool_arguments(tool_call: ToolCall) -> ToolArguments: ...


@overload
def tool_arguments(tool_call: ToolCallItem) -> ToolArguments: ...


def tool_arguments(tool_call: ToolCall | ToolCallItem) -> ToolArguments:
    """Decode arguments from a stream or message tool call."""
    if isinstance(tool_call, ToolCall):
        return tool_call.arguments_dict()
    return parse_tool_arguments(tool_call["arguments"], tool_name=tool_call["name"])


def tool_call_item(tool_call: ToolCall) -> ToolCallItem:
    """Convert a streamed tool call into an assistant message item."""
    return {
        "type": "tool_call",
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": tool_call.arguments,
    }


def is_text_item(item: MessageItem) -> TypeGuard[TextItem]:
    return item["type"] == "text"


def is_tool_call_item(item: MessageItem) -> TypeGuard[ToolCallItem]:
    return item["type"] == "tool_call"


def is_tool_result_item(item: MessageItem) -> TypeGuard[ToolResultItem]:
    return item["type"] == "tool_result"


def is_thinking_item(item: MessageItem) -> TypeGuard[ThinkingItem]:
    return item["type"] == "thinking"


def is_redacted_thinking_item(item: MessageItem) -> TypeGuard[RedactedThinkingItem]:
    return item["type"] == "redacted_thinking"


def is_image_item(item: MessageItem) -> TypeGuard[ImageItem]:
    return item["type"] == "image_url"


def is_audio_item(item: MessageItem) -> TypeGuard[AudioItem]:
    return item["type"] == "input_audio"


def is_file_item(item: MessageItem) -> TypeGuard[FileItem]:
    return item["type"] == "file"


def render_item_text(
    item: MessageItem | ToolCall | Response | Reasoning,
) -> str:
    """Render a readable text form for a message or stream item."""
    if isinstance(item, Response):
        return render_item_text(item.item)
    if isinstance(item, Reasoning):
        return render_item_text(item.item)
    if isinstance(item, ToolCall):
        return f"{item.name}({item.arguments})"
    if is_text_item(item):
        return item["text"]
    if is_image_item(item):
        url = item["image_url"]["url"]
        return "<base64 image>" if url.startswith("data:") else f"<image {url}>"
    if is_audio_item(item):
        return "<audio>"
    if is_file_item(item):
        return "<file>"
    if is_tool_call_item(item):
        return f"{item['name']}({item['arguments']})"
    if is_tool_result_item(item):
        content = item["content"]
        if isinstance(content, str):
            return content
        return "".join(render_item_text(sub_item) for sub_item in content)
    if is_thinking_item(item):
        return f"<thinking>{item['thinking']}</thinking>"
    if is_redacted_thinking_item(item):
        return "<redacted_thinking>"
    raise AssertionError(f"Unsupported item type: {item['type']}")


def render_message_text(message: Message) -> str:
    """Render a readable text form for a whole message."""
    return "".join(render_item_text(item) for item in message.content)


@overload
def with_cache_control(
    item: ToolCallItem, cache_control: CacheControl
) -> ToolCallItem: ...


@overload
def with_cache_control(
    item: ToolResultItem, cache_control: CacheControl
) -> ToolResultItem: ...


@overload
def with_cache_control(item: TextItem, cache_control: CacheControl) -> TextItem: ...


@overload
def with_cache_control(item: ImageItem, cache_control: CacheControl) -> ImageItem: ...


@overload
def with_cache_control(item: AudioItem, cache_control: CacheControl) -> AudioItem: ...


@overload
def with_cache_control(item: FileItem, cache_control: CacheControl) -> FileItem: ...


@overload
def with_cache_control(
    item: ThinkingItem, cache_control: CacheControl
) -> ThinkingItem: ...


@overload
def with_cache_control(
    item: RedactedThinkingItem, cache_control: CacheControl
) -> RedactedThinkingItem: ...


def with_cache_control(item: MessageItem, cache_control: CacheControl) -> MessageItem:
    """Return a copy of *item* with provider cache metadata attached."""
    if is_text_item(item):
        return {
            "type": "text",
            "text": item["text"],
            "cache_control": cache_control,
        }
    if is_tool_call_item(item):
        return {
            "type": "tool_call",
            "id": item["id"],
            "name": item["name"],
            "arguments": item["arguments"],
            "cache_control": cache_control,
        }
    if is_tool_result_item(item):
        return {
            "type": "tool_result",
            "tool_call_id": item["tool_call_id"],
            "content": item["content"],
            "cache_control": cache_control,
        }
    if is_image_item(item):
        return {
            "type": "image_url",
            "image_url": item["image_url"],
            "cache_control": cache_control,
        }
    if is_audio_item(item):
        return {
            "type": "input_audio",
            "input_audio": item["input_audio"],
            "cache_control": cache_control,
        }
    if is_file_item(item):
        return {
            "type": "file",
            "file": item["file"],
            "cache_control": cache_control,
        }
    if is_thinking_item(item):
        result_t: ThinkingItem = {
            "type": "thinking",
            "thinking": item["thinking"],
            "cache_control": cache_control,
        }
        sig = item.get("signature")
        if sig is not None:
            result_t["signature"] = sig
        return result_t
    if is_redacted_thinking_item(item):
        return {
            "type": "redacted_thinking",
            "data": item["data"],
            "cache_control": cache_control,
        }
    raise AssertionError(f"Unsupported item type: {item['type']}")


def to_plain_dict(item: MessageItem) -> dict[str, Any]:
    """Convert a TypedDict item into a plain dict for provider SDK payloads."""
    result: dict[str, Any]
    if is_text_item(item):
        result = {"type": "text", "text": item["text"]}
    elif is_tool_call_item(item):
        result = {
            "type": "tool_call",
            "id": item["id"],
            "name": item["name"],
            "arguments": item["arguments"],
        }
    elif is_tool_result_item(item):
        content: str | list[dict[str, Any]]
        if isinstance(item["content"], str):
            content = item["content"]
        else:
            content = [to_plain_dict(block) for block in item["content"]]
        result = {
            "type": "tool_result",
            "tool_call_id": item["tool_call_id"],
            "content": content,
        }
    elif is_image_item(item):
        result = {
            "type": "image_url",
            "image_url": dict(item["image_url"]),
        }
    elif is_audio_item(item):
        result = {
            "type": "input_audio",
            "input_audio": dict(item["input_audio"]),
        }
    elif is_file_item(item):
        result = {
            "type": "file",
            "file": dict(item["file"]),
        }
    elif is_thinking_item(item):
        result = {"type": "thinking", "thinking": item["thinking"]}
        sig = item.get("signature")
        if sig is not None:
            result["signature"] = sig
    elif is_redacted_thinking_item(item):
        result = {"type": "redacted_thinking", "data": item["data"]}
    else:
        raise AssertionError(f"Unsupported item type: {item['type']}")
    if "cache_control" in item:
        result["cache_control"] = dict(item["cache_control"])
    return result


def with_last_item_cache_control(
    message: Message, cache_control: CacheControl
) -> Message:
    """Return a copy of *message* with cache metadata on its final item."""
    items = list(message.content)
    items[-1] = with_cache_control(items[-1], cache_control)
    return Message(message.role, items, dict(message.provider_extra))


# ---------------------------------------------------------------------------
# Usage & Cost
# ---------------------------------------------------------------------------


class Usage(msgspec.Struct, frozen=True):
    """Token usage reported by the API."""

    provider: str
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int | None = None


class Cost(msgspec.Struct, frozen=True):
    """Cost breakdown for a single request.  All amounts in USD."""

    input_cost: float
    output_cost: float
    total_cost: float
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    source: str = ""  # "provider" | "yaml" | "genai-prices"


# ---------------------------------------------------------------------------
# Type alias for the stream return
# ---------------------------------------------------------------------------


class Store(msgspec.Struct):
    """Mutable metadata populated after the stream is exhausted."""

    usage: Usage | None = None
    cost: Cost | None = None
    provider_cost: float | None = None
    recoveries: list[AttemptRecovery] = msgspec.field(default_factory=list)


StreamResult = tuple[AsyncIterator[StreamItem], Store]
"""Return type of ``Provider.stream()`` and ``YLLMClient.stream()``."""


# ---------------------------------------------------------------------------
# Raw chunk hook
# ---------------------------------------------------------------------------

RawChunkHook = Callable[[Any], None]
"""Callback invoked with every raw provider chunk before yuullm processes it.

The chunk type depends on the provider:
- OpenAI: ``openai.types.chat.ChatCompletionChunk``
- Anthropic: an event object with ``.type`` attribute

This is the escape hatch for consumers who need provider-level visibility
without abandoning yuullm's streaming abstraction.
"""


def on_tool_call_name(name: str, callback: Callable[[int], None]) -> RawChunkHook:
    """Helper hook: fires *callback(index)* when a tool call's name matches.

    Works with both OpenAI and Anthropic raw chunks.  The callback is
    invoked at most once per tool-call index.

    If the LLM emits the name after the arguments (unlikely but possible),
    the notification will simply arrive late -- that's the caller's bad luck.

    Parameters
    ----------
    name : str
        The tool / function name to watch for.
    callback : Callable[[int], None]
        Called with the tool-call index the first time *name* is seen.
    """
    seen: set[int] = set()

    def hook(chunk: Any) -> None:
        # --- OpenAI path ---
        choices = getattr(chunk, "choices", None)
        if choices:
            delta = choices[0].delta if choices else None
            tc_deltas = getattr(delta, "tool_calls", None) if delta else None
            if tc_deltas:
                for tc_delta in tc_deltas:
                    idx = tc_delta.index
                    if idx in seen:
                        continue
                    fn = getattr(tc_delta, "function", None)
                    if fn and getattr(fn, "name", None) == name:
                        seen.add(idx)
                        callback(idx)
            return

        # --- Anthropic path ---
        event_type = getattr(chunk, "type", None)
        if event_type == "content_block_start":
            block = getattr(chunk, "content_block", None)
            if block and getattr(block, "type", None) == "tool_use":
                idx = getattr(chunk, "index", -1)
                if idx not in seen and getattr(block, "name", None) == name:
                    seen.add(idx)
                    callback(idx)

    return hook


# ---------------------------------------------------------------------------
# Provider pool types
# ---------------------------------------------------------------------------


class ProviderSpec(msgspec.Struct, frozen=True):
    """Configuration needed to construct a yuullm.Provider."""

    name: str  # e.g. "deepseek", "openrouter"
    api_type: str  # "openai-chat-completion" | "anthropic-messages"
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    extra: dict[str, object] = msgspec.field(default_factory=dict)


class ModelBinding(msgspec.Struct, frozen=True):
    """A resolved (provider, model_id) pair for a selector."""

    provider_name: str  # e.g. "deepseek"
    model: str  # e.g. "deepseek-v4-pro"
    source: str = ""  # "cached" | "exact" | "substring" | "llm_pick" | "heuristic"


class CallRecord(msgspec.Struct, frozen=True):
    """Timing and usage record for a single provider.stream() call."""

    provider_name: str
    model: str
    selector: str  # model selector that was resolved
    started_at: float  # time.monotonic() timestamp
    finished_at: float | None = None  # time.monotonic() timestamp
    usage: Usage | None = None
    error: str | None = None  # error message if failed
