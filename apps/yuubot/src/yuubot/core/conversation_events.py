"""Frontend-facing conversation SSE protocol projection."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

import msgspec
from yuuagents.core.eventbus import RuntimeEvent

ConversationFrontendEventType = Literal[
    "turn_started",
    "assistant_delta",
    "tool_call_started",
    "tool_output_snapshot",
    "tool_result_committed",
    "message_committed",
    "turn_completed",
    "error",
]

_ANSI_PATTERN = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]"
)


@dataclass(frozen=True)
class ConversationFrontendEvent:
    conversation_id: str
    event_id: str
    sequence: int
    event_type: ConversationFrontendEventType
    timestamp: float
    payload: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            **self.payload,
        }


@dataclass(frozen=True)
class _ToolOutputKey:
    conversation_id: str
    tool_call_id: str


@dataclass
class ConversationSSEProjector:
    """Projects internal runtime events into the stable Admin UI SSE protocol."""

    _sequence_by_conversation: dict[str, int] = field(default_factory=dict)
    _tool_output_chunks: dict[_ToolOutputKey, list[str]] = field(default_factory=dict)

    def project_runtime_event(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        if event.name == "output.chunk":
            return self._project_output_chunk(conversation_id, event)
        if event.name in {"agent.turn.error", "budget.exceeded"}:
            return [self.error(conversation_id, event, _event_error(event))]
        if event.name == "agent.turn_started":
            return [self.turn_started(conversation_id, event)]
        if event.name == "agent.turn_completed":
            return [self.turn_completed(conversation_id, event)]
        return []

    def message_committed(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        *,
        turn_id: str,
        message_id: str,
        role: str,
        content: list[dict[str, object]],
    ) -> ConversationFrontendEvent:
        return self._event(
            conversation_id,
            event,
            "message_committed",
            {
                "turn_id": turn_id,
                "message_id": message_id,
                "role": role,
                "content": content,
            },
        )

    def tool_result_committed(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        *,
        turn_id: str,
        message_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        content: str,
    ) -> ConversationFrontendEvent:
        return self._event(
            conversation_id,
            event,
            "tool_result_committed",
            {
                "turn_id": turn_id,
                "message_id": message_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": status,
                "content": content,
            },
        )

    def error(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        error: str,
    ) -> ConversationFrontendEvent:
        return self._event(
            conversation_id,
            event,
            "error",
            {"turn_id": _turn_id(event), "error": error},
        )

    def turn_started(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> ConversationFrontendEvent:
        return self._event(
            conversation_id,
            event,
            "turn_started",
            {
                "turn_id": _turn_id(event),
                "agent_id": event.agent_id or "",
                "agent_name": event.agent_name or "",
            },
        )

    def turn_completed(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> ConversationFrontendEvent:
        return self._event(
            conversation_id,
            event,
            "turn_completed",
            {
                "turn_id": _turn_id(event),
                "agent_id": event.agent_id or "",
                "agent_name": event.agent_name or "",
            },
        )

    def _project_output_chunk(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        data = _chunk_data(event)
        if data.parent_id or data.tool_call_id:
            return [self._tool_output_snapshot(conversation_id, event, data)]

        events: list[ConversationFrontendEvent] = []
        assistant_blocks = _assistant_blocks(data.blocks)
        if assistant_blocks:
            events.append(
                self._event(
                    conversation_id,
                    event,
                    "assistant_delta",
                    {"turn_id": _turn_id(event), "blocks": assistant_blocks},
                )
            )
        for call in _tool_call_blocks(data.blocks):
            events.append(
                self._event(
                    conversation_id,
                    event,
                    "tool_call_started",
                    {
                        "turn_id": _turn_id(event),
                        "tool_call_id": call.tool_call_id,
                        "tool_name": call.tool_name,
                        "arguments": call.arguments,
                    },
                )
            )
        return events

    def _tool_output_snapshot(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        data: "_ChunkData",
    ) -> ConversationFrontendEvent:
        tool_call_id = data.tool_call_id or data.parent_id or data.entity_id
        key = _ToolOutputKey(conversation_id=conversation_id, tool_call_id=tool_call_id)
        chunks = self._tool_output_chunks.setdefault(key, [])
        chunks.append(_blocks_text(data.blocks))
        return self._event(
            conversation_id,
            event,
            "tool_output_snapshot",
            {
                "turn_id": _turn_id(event),
                "tool_call_id": tool_call_id,
                "tool_name": data.tool_name,
                "stream": data.stream,
                "format": "terminal",
                "revision": data.chunk_index,
                "content": render_tool_output_final_text(chunks),
                "complete": False,
            },
        )

    def _event(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        event_type: ConversationFrontendEventType,
        payload: dict[str, object],
    ) -> ConversationFrontendEvent:
        return ConversationFrontendEvent(
            conversation_id=conversation_id,
            event_id=uuid.uuid4().hex,
            sequence=self._next_sequence(conversation_id),
            event_type=event_type,
            timestamp=event.timestamp,
            payload={key: value for key, value in payload.items() if value is not None},
        )

    def _next_sequence(self, conversation_id: str) -> int:
        sequence = self._sequence_by_conversation.get(conversation_id, 0) + 1
        self._sequence_by_conversation[conversation_id] = sequence
        return sequence


@dataclass(frozen=True)
class _ChunkData:
    entity_id: str
    parent_id: str
    tool_call_id: str
    tool_name: str
    stream: str
    chunk_index: int
    blocks: tuple[object, ...]


@dataclass(frozen=True)
class _ToolCallData:
    tool_call_id: str
    tool_name: str
    arguments: object


def render_tool_output_final_text(raw_chunks: str | Iterable[str]) -> str:
    raw_text = raw_chunks if isinstance(raw_chunks, str) else "".join(raw_chunks)
    clean_text = _ANSI_PATTERN.sub("", raw_text)
    rendered = _render_carriage_returns(clean_text)
    return _render_backspaces_and_strip_controls(rendered)


def _render_carriage_returns(text: str) -> str:
    lines: list[str] = []
    current: list[str] = []
    replace_buffer: list[str] | None = None
    for char in text:
        if char == "\r":
            if replace_buffer is not None:
                current = replace_buffer
            replace_buffer = []
            continue
        if char == "\n":
            if replace_buffer is not None:
                current.extend(replace_buffer)
                replace_buffer = None
            lines.append("".join(current))
            current = []
            continue
        if replace_buffer is not None:
            replace_buffer.append(char)
        else:
            current.append(char)
    if replace_buffer is not None:
        if replace_buffer:
            current = replace_buffer
    if current:
        lines.append("".join(current))
    if text.endswith("\n"):
        return "\n".join(lines) + "\n"
    return "\n".join(lines)


def _render_backspaces_and_strip_controls(text: str) -> str:
    result: list[str] = []
    for char in text:
        if char == "\b":
            if result and result[-1] != "\n":
                result.pop()
            continue
        if _is_unsupported_control(char):
            continue
        result.append(char)
    return "".join(result)


def _chunk_data(event: RuntimeEvent) -> _ChunkData:
    data = event.data
    raw_blocks = data.get("blocks", [])
    blocks = tuple(raw_blocks) if isinstance(raw_blocks, list) else ()
    return _ChunkData(
        entity_id=str(data.get("entity_id") or ""),
        parent_id=str(data.get("parent_id") or ""),
        tool_call_id=str(data.get("tool_call_id") or ""),
        tool_name=str(data.get("tool_name") or data.get("name") or "tool"),
        stream=_stream(data.get("stream")),
        chunk_index=_int_value(data.get("chunk_index")),
        blocks=blocks,
    )


def _assistant_blocks(blocks: tuple[object, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for block in blocks:
        source = _block_source(block)
        block_type = str(source.get("type") or "")
        if block_type == "text" and isinstance(source.get("text"), str):
            result.append({"type": "text", "text": str(source["text"])})
        if block_type == "thinking" and isinstance(source.get("thinking"), str):
            result.append({"type": "thinking", "thinking": str(source["thinking"])})
    return result


def _tool_call_blocks(blocks: tuple[object, ...]) -> list[_ToolCallData]:
    result: list[_ToolCallData] = []
    for block in blocks:
        source = _block_source(block)
        if source.get("type") != "tool_call":
            continue
        result.append(
            _ToolCallData(
                tool_call_id=str(source.get("id") or source.get("tool_call_id") or ""),
                tool_name=str(source.get("name") or source.get("tool_name") or "tool"),
                arguments=msgspec.to_builtins(source.get("arguments", {})),
            )
        )
    return result


def _blocks_text(blocks: tuple[object, ...]) -> str:
    return "".join(_block_text(block) for block in blocks)


def _block_text(block: object) -> str:
    source = _block_source(block)
    text = source.get("text")
    if isinstance(text, str):
        return text
    content = source.get("content")
    if isinstance(content, str):
        return content
    return "" if source.get("type") == "tool_call" else str(content or "")


def _block_source(block: object) -> dict[str, object]:
    raw = msgspec.to_builtins(block)
    if not isinstance(raw, dict):
        return {"type": "text", "text": str(raw)}
    content = raw.get("content")
    if isinstance(content, dict):
        return {str(key): value for key, value in content.items()}
    return {str(key): value for key, value in raw.items()}


def _event_error(event: RuntimeEvent) -> str:
    error = event.data.get("error")
    return str(error or event.name)


def _turn_id(event: RuntimeEvent) -> str:
    data = event.data
    return str(data.get("turn_id") or data.get("task_id") or event.agent_id or "")


def _stream(value: object) -> str:
    if value in {"stdout", "stderr", "combined"}:
        return str(value)
    return "combined"


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _is_unsupported_control(char: str) -> bool:
    return ord(char) < 32 and char not in {"\t", "\n", "\r", "\b"}
