"""Stream protocol types, event merging, and cost estimation."""

from typing import Literal, cast

import msgspec

from .messages import GenAudio, GenImage, GenOutput, GenReasoning, GenText, GenToolCall, ModelCard

StopReason = Literal["stop", "length", "tool_calls", "content_filter", "function_call", "interrupted"]
StreamKind = Literal[
    "text_delta",
    "reasoning_delta",
    "tool_name",
    "tool_arguments_delta",
    "tool_arguments_end",
    "stream_stop",
]


class Usage(msgspec.Struct, frozen=True, kw_only=True):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    payg_cost: float | None = None


class StreamStop(msgspec.Struct, frozen=True, kw_only=True):
    reason: StopReason
    usage: Usage = msgspec.field(default_factory=Usage)
    account: dict[str, object] = msgspec.field(default_factory=dict)
    cost_estimated: bool = False


class StreamEvent(msgspec.Struct, frozen=True, kw_only=True):
    group_id: str
    kind: StreamKind
    payload: dict[str, object] = msgspec.field(default_factory=dict)


class ToolCall(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    arguments: str


def estimate_cost(model: ModelCard, usage: Usage) -> float:
    return (
        usage.input_tokens * model.input_price_per_million
        + usage.cached_input_tokens * model.cached_input_price_per_million
        + usage.output_tokens * model.output_price_per_million
    ) / 1_000_000


def merge(events: list[StreamEvent], *, drop_partial_toolcall: bool = True) -> tuple[list[GenOutput], StreamStop]:
    text: dict[str, list[str]] = {}
    reasoning: dict[str, list[str]] = {}
    calls: dict[str, dict[str, str]] = {}
    order: list[tuple[str, str]] = []
    stop = StreamStop(reason="stop")

    for event in events:
        if event.kind == "stream_stop":
            payload = event.payload
            stop = StreamStop(
                reason=cast(StopReason, payload.get("reason", "stop")),
                usage=msgspec.convert(payload.get("usage", {}), Usage),
                account=msgspec.convert(payload.get("account", {}), dict[str, object]),
                cost_estimated=bool(payload.get("cost_estimated", False)),
            )
        elif event.kind == "text_delta":
            _mark(order, "text", event.group_id)
            text.setdefault(event.group_id, []).append(_payload_str(event.payload, "text"))
        elif event.kind == "reasoning_delta":
            _mark(order, "reasoning", event.group_id)
            reasoning.setdefault(event.group_id, []).append(_payload_str(event.payload, "text"))
        elif event.kind == "tool_name":
            _mark(order, "tool", event.group_id)
            calls.setdefault(event.group_id, {})["name"] = _payload_str(event.payload, "name")
            calls[event.group_id]["id"] = _payload_str(event.payload, "id", event.group_id)
        elif event.kind == "tool_arguments_delta":
            _mark(order, "tool", event.group_id)
            calls.setdefault(event.group_id, {}).setdefault("arguments", "")
            calls[event.group_id]["arguments"] += _payload_str(event.payload, "text")
        elif event.kind == "tool_arguments_end":
            calls.setdefault(event.group_id, {})["done"] = "1"

    outputs: list[GenOutput] = []
    for kind, group_id in order:
        if kind == "text":
            outputs.append(GenText(text="".join(text.get(group_id, []))))
        elif kind == "reasoning":
            outputs.append(GenReasoning(text="".join(reasoning.get(group_id, []))))
        else:
            call = calls.get(group_id, {})
            if call.get("done") or not drop_partial_toolcall:
                outputs.append(GenToolCall(id=call.get("id", group_id), name=call.get("name", ""), arguments=call.get("arguments", "")))
    return outputs, stop


def extract_tool_calls(outputs: list[GenOutput]) -> list[ToolCall]:
    return [ToolCall(id=item.id, name=item.name, arguments=item.arguments) for item in outputs if isinstance(item, GenToolCall)]


def _mark(order: list[tuple[str, str]], kind: str, group_id: str) -> None:
    key = (kind, group_id)
    if key not in order:
        order.append(key)


def _payload_str(payload: dict[str, object], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    return value if isinstance(value, str) else default
