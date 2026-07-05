"""History items, context trees, and message payloads."""

from pathlib import Path
from typing import Literal, TypeAlias

import msgspec

ContentKind = Literal["text", "image", "audio", "file"]
InputRole = Literal["user", "developer"]


class ModelCard(msgspec.Struct, frozen=True, kw_only=True):
    selector: str
    reasoning_effort: str = ""
    vision: bool = False
    toolcall: bool = True
    json: bool = True
    input_price_per_million: float = 0
    cached_input_price_per_million: float = 0
    output_price_per_million: float = 0


class ContentItem(msgspec.Struct, frozen=True, kw_only=True):
    kind: ContentKind
    text: str = ""
    path: str = ""
    url: str = ""
    mime: str = "text/plain"
    meta: dict[str, object] = msgspec.field(default_factory=dict)


def text_content(text: str) -> list[ContentItem]:
    return [ContentItem(kind="text", text=text)]


class InputMessage(msgspec.Struct, frozen=True, kw_only=True):
    role: InputRole
    name: str
    content: list[ContentItem]


class GenText(msgspec.Struct, frozen=True, kw_only=True):
    text: str


class GenReasoning(msgspec.Struct, frozen=True, kw_only=True):
    text: str


class GenToolCall(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    arguments: str


class GenImage(msgspec.Struct, frozen=True, kw_only=True):
    content: list[ContentItem]


class GenAudio(msgspec.Struct, frozen=True, kw_only=True):
    content: list[ContentItem]


class ToolResult(msgspec.Struct, frozen=True, kw_only=True):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    content: list[ContentItem]


class HistoryToolSpecs(msgspec.Struct, frozen=True, kw_only=True):
    specs: list[dict[str, object]]


class SystemPrompt(msgspec.Struct, frozen=True, kw_only=True):
    text: str


GenOutput: TypeAlias = GenText | GenReasoning | GenToolCall | GenImage | GenAudio
HistoryItem: TypeAlias = HistoryToolSpecs | SystemPrompt | InputMessage | GenOutput | ToolResult


class ActorMessage(msgspec.Struct, frozen=True, kw_only=True):
    """Message delivered to an actor mailbox by WakeupDelivery."""

    text: str
    conversation_id: str | None = None
    source: dict[str, object] = msgspec.field(default_factory=dict)


class ConversationContext(msgspec.Struct, frozen=True, kw_only=True):
    """Read-only context tree shared by every unit of one conversation."""

    model: ModelCard
    conversation_id: str
    actor: str
    workspace: Path
    integrations: dict[str, dict[str, str]] = msgspec.field(default_factory=dict)
    otel: dict[str, object] = msgspec.field(default_factory=dict)
    rpc: dict[str, object] = msgspec.field(default_factory=dict)


class LLMInput(msgspec.Struct, frozen=True, kw_only=True):
    tool_specs: list[dict[str, object]]
    messages: list[HistoryItem]
