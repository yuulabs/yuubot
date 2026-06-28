"""Frontend-facing conversation SSE protocol projection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

import msgspec
from yuuagents.core.eventbus import RuntimeEvent

from .rendering import render_tool_output_final_text

ConversationFrontendEventType = Literal[
    "turn_started",
    "transcript_delta",
    "turn_completed",
    "cost_update",
    "error",
]


@dataclass(frozen=True)
class ConversationSSEHeartbeat:
    """In-band keepalive marker yielded by ``subscribe_events``.

    Long-lived SSE streams sit idle between turns; without a periodic frame
    the daemon-to-admin HTTP hop and any proxying middleboxes close the
    connection on idle timeout and ``EventSource`` reconnects silently.

    The projector never produces this — it is synthesised by
    ``ConversationManager.subscribe_events`` when its queue.get() times out.
    The daemon SSE handler renders it as an SSE comment frame ``: heartbeat\\n\\n``
    (no event_type) which the browser ``EventSource`` silently discards
    while keeping the connection alive.
    """

    conversation_id: str

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
    _tool_result_text_by_key: dict[_ToolOutputKey, str] = field(default_factory=dict)

    def drop_conversation(self, conversation_id: str) -> None:
        self._sequence_by_conversation.pop(conversation_id, None)
        for key in list(self._tool_result_text_by_key):
            if key.conversation_id == conversation_id:
                self._tool_result_text_by_key.pop(key, None)

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

    def transcript_delta(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        *,
        turn_id: str,
        deltas: list[dict[str, object]],
    ) -> ConversationFrontendEvent:
        return self._event(
            conversation_id,
            event,
            "transcript_delta",
            {"turn_id": turn_id, "deltas": deltas},
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
        """Emit a named ``turn_completed`` event without closing the stream.

        The frontend depends on this event to flip ``isSending`` off and
        mark live tool blocks as completed. Closing the HTTP stream on turn
        completion (the prior regression) meant the next send had no
        subscriber and silently dropped every delta — see the scenario in
        ``subscribe_events``.
        """
        return self._event(
            conversation_id,
            event,
            "turn_completed",
            {"turn_id": _turn_id(event)},
        )

    def cost_update(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        *,
        turn_cost: float,
        total_cost: float,
    ) -> ConversationFrontendEvent:
        """Project an ``llm.finished`` event into a ``cost_update`` SSE event.

        The frontend renders this as ``$X spent`` (no quota display in this
        phase — quota / progress-to-ceiling is Phase 5-3's responsibility).
        ``turn_cost`` is the cost of the single LLM call that just
        finished; ``total_cost`` is the running cumulative USD spend held
        by the in-memory ``Budget`` for this agent (falling back to
        ``turn_cost`` when the Budget is unavailable, e.g. on the cold
        first call after a daemon restart).
        """
        return self._event(
            conversation_id,
            event,
            "cost_update",
            {"turn_cost": turn_cost, "total_cost": total_cost},
        )

    def missing_tool_result_delta(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        *,
        tool_call_id: str,
        tool_name: str,
        text: str,
    ) -> ConversationFrontendEvent | None:
        key = _ToolOutputKey(conversation_id=conversation_id, tool_call_id=tool_call_id)
        streamed = self._tool_result_text_by_key.get(key, "")
        missing = text.removeprefix(streamed) if text.startswith(streamed) else text
        if not missing:
            return None
        self._tool_result_text_by_key[key] = f"{streamed}{missing}"
        return self.transcript_delta(
            conversation_id,
            event,
            turn_id=_turn_id(event),
            deltas=[{
                "type": "tool_result",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "text_delta": missing,
            }],
        )

    def _project_output_chunk(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        data = _chunk_data(event)
        if data.parent_id or data.tool_call_id:
            return [self._tool_result_delta(conversation_id, event, data)]

        deltas = _assistant_deltas(data.blocks) + _tool_call_deltas(data.blocks)
        if not deltas:
            return []
        return [
            self.transcript_delta(
                conversation_id,
                event,
                turn_id=_turn_id(event),
                deltas=deltas,
            )
        ]

    def _tool_result_delta(
        self,
        conversation_id: str,
        event: RuntimeEvent,
        data: "_ChunkData",
    ) -> ConversationFrontendEvent:
        tool_call_id = data.tool_call_id or data.parent_id or data.entity_id
        key = _ToolOutputKey(conversation_id=conversation_id, tool_call_id=tool_call_id)
        text_delta = render_tool_output_final_text(_blocks_text(data.blocks))
        self._tool_result_text_by_key[key] = (
            self._tool_result_text_by_key.get(key, "") + text_delta
        )
        return self.transcript_delta(
            conversation_id,
            event,
            turn_id=_turn_id(event),
            deltas=[{
                "type": "tool_result",
                "tool_call_id": tool_call_id,
                "tool_name": data.tool_name,
                "stream": data.stream,
                "text_delta": text_delta,
            }],
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


def _assistant_deltas(blocks: tuple[object, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for block in blocks:
        source = _block_source(block)
        block_type = str(source.get("type") or "")
        if block_type == "text" and isinstance(source.get("text"), str):
            result.append({"type": "text", "text_delta": str(source["text"])})
        if block_type == "thinking" and isinstance(source.get("thinking"), str):
            result.append({
                "type": "thinking",
                "text_delta": str(source["thinking"]),
            })
    return result


def _tool_call_deltas(blocks: tuple[object, ...]) -> list[dict[str, object]]:
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
    return [_tool_call_delta(call) for call in result]


def _tool_call_delta(call: _ToolCallData) -> dict[str, object]:
    delta: dict[str, object] = {
        "type": "tool_call",
        "tool_call_id": call.tool_call_id,
        "tool_name": call.tool_name,
    }
    if isinstance(call.arguments, str):
        delta["arguments_text_delta"] = call.arguments
    else:
        delta["arguments_delta"] = call.arguments
    return delta


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
