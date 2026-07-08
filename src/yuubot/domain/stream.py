"""Stream protocol types, event merging, and cost estimation."""

from typing import Literal

import msgspec

from .messages import GenOutput, GenReasoning, GenText, GenToolCall, ModelCard

StopReason = Literal["stop", "length", "tool_calls", "content_filter", "function_call", "interrupted"]
StreamKind = Literal[
    "text_delta",
    "reasoning_delta",
    "tool_name",
    "tool_arguments_delta",
    "tool_arguments_end",
    "tool_result_delta",
    "tool_result_end",
    "stream_stop",
]


class Usage(msgspec.Struct, frozen=True):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    payg_cost: float | None = None


class StreamStop(msgspec.Struct, frozen=True):
    reason: StopReason
    usage: Usage = msgspec.field(default_factory=Usage)
    account: dict[str, object] = msgspec.field(default_factory=dict)
    cost_estimated: bool = False


class TextDeltaPayload(msgspec.Struct, frozen=True):
    text: str = ""


class ReasoningDeltaPayload(msgspec.Struct, frozen=True):
    text: str = ""


class ToolNamePayload(msgspec.Struct, frozen=True):
    name: str = ""
    id: str = ""


class ToolArgumentsDeltaPayload(msgspec.Struct, frozen=True):
    text: str = ""


class ToolArgumentsEndPayload(msgspec.Struct, frozen=True):
    pass


class ToolResultDeltaPayload(msgspec.Struct, frozen=True):
    tool_call_id: str = ""
    tool_name: str = ""
    text: str = ""


class ToolResultEndPayload(msgspec.Struct, frozen=True):
    tool_call_id: str = ""
    tool_name: str = ""
    content: list[object] = msgspec.field(default_factory=list)


class StreamStopPayload(msgspec.Struct, frozen=True):
    reason: StopReason = "stop"
    usage: Usage = msgspec.field(default_factory=Usage)
    account: dict[str, object] = msgspec.field(default_factory=dict)
    cost_estimated: bool = False


StreamEventPayload = (
    TextDeltaPayload
    | ReasoningDeltaPayload
    | ToolNamePayload
    | ToolArgumentsDeltaPayload
    | ToolArgumentsEndPayload
    | ToolResultDeltaPayload
    | ToolResultEndPayload
    | StreamStopPayload
)


class StreamEvent(msgspec.Struct, frozen=True):
    group_id: str
    kind: StreamKind
    payload: StreamEventPayload = msgspec.field(default_factory=ToolArgumentsEndPayload)


class ToolCall(msgspec.Struct, frozen=True):
    id: str
    name: str
    arguments: str


def estimate_cost(model: ModelCard, usage: Usage) -> float:
    input_price = model.input_price_per_million
    cached_price = model.cached_input_price_per_million
    output_price = model.output_price_per_million
    if input_price is None or cached_price is None or output_price is None:
        return 0.0
    return (
        usage.input_tokens * input_price
        + usage.cached_input_tokens * cached_price
        + usage.output_tokens * output_price
    ) / 1_000_000


def merge(events: list[StreamEvent], drop_partial_toolcall: bool = True) -> tuple[list[GenOutput], StreamStop]:
    text: dict[str, list[str]] = {}
    reasoning: dict[str, list[str]] = {}
    calls: dict[str, dict[str, str]] = {}
    order: list[tuple[str, str]] = []
    stop = StreamStop("stop")

    for event in events:
        if event.kind == "stream_stop":
            payload = event.payload
            if not isinstance(payload, StreamStopPayload):
                raise TypeError("stream_stop event requires StreamStopPayload")
            stop = StreamStop(
                payload.reason,
                payload.usage,
                payload.account,
                payload.cost_estimated,
            )
        elif event.kind == "text_delta":
            payload = event.payload
            if not isinstance(payload, TextDeltaPayload):
                raise TypeError("text_delta event requires TextDeltaPayload")
            _mark(order, "text", event.group_id)
            text.setdefault(event.group_id, []).append(payload.text)
        elif event.kind == "reasoning_delta":
            payload = event.payload
            if not isinstance(payload, ReasoningDeltaPayload):
                raise TypeError("reasoning_delta event requires ReasoningDeltaPayload")
            _mark(order, "reasoning", event.group_id)
            reasoning.setdefault(event.group_id, []).append(payload.text)
        elif event.kind == "tool_name":
            payload = event.payload
            if not isinstance(payload, ToolNamePayload):
                raise TypeError("tool_name event requires ToolNamePayload")
            _mark(order, "tool", event.group_id)
            calls.setdefault(event.group_id, {})["name"] = payload.name
            calls[event.group_id]["id"] = payload.id or event.group_id
        elif event.kind == "tool_arguments_delta":
            payload = event.payload
            if not isinstance(payload, ToolArgumentsDeltaPayload):
                raise TypeError("tool_arguments_delta event requires ToolArgumentsDeltaPayload")
            _mark(order, "tool", event.group_id)
            calls.setdefault(event.group_id, {}).setdefault("arguments", "")
            calls[event.group_id]["arguments"] += payload.text
        elif event.kind == "tool_arguments_end":
            calls.setdefault(event.group_id, {})["done"] = "1"

    outputs: list[GenOutput] = []
    for kind, group_id in order:
        if kind == "text":
            outputs.append(GenText("".join(text.get(group_id, []))))
        elif kind == "reasoning":
            outputs.append(GenReasoning("".join(reasoning.get(group_id, []))))
        else:
            call = calls.get(group_id, {})
            if call.get("done") or not drop_partial_toolcall:
                outputs.append(GenToolCall(call.get("id", group_id), call.get("name", ""), call.get("arguments", "")))
    return outputs, stop


def extract_tool_calls(outputs: list[GenOutput]) -> list[ToolCall]:
    return [ToolCall(item.id, item.name, item.arguments) for item in outputs if isinstance(item, GenToolCall)]


def _mark(order: list[tuple[str, str]], kind: str, group_id: str) -> None:
    key = (kind, group_id)
    if key not in order:
        order.append(key)
