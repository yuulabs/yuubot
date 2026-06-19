from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import msgspec
import yuullm
import yuutrace

type EventValue = (
    None
    | bool
    | int
    | float
    | str
    | list[object]
    | dict[str, object]
    | msgspec.Struct
    | yuullm.ContentItem
    | yuullm.MessageItem
    | yuullm.Message
    | yuullm.ToolCall
    | yuutrace.LlmUsage
    | yuutrace.LlmCost
    | Sequence[object]
    | Mapping[str, object]
)
type EventData = dict[str, EventValue]
type EventPayload = Mapping[str, EventValue]

type ToolPayload = dict[str, object]
type ToolConfig = dict[str, object]
type ToolSchema = dict[str, object]
type LlmOptions = dict[str, object]


def validate_json_value(value: object, path: str = "$") -> object:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path} must be a finite JSON number")
        return value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [validate_json_value(item, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            out[key] = validate_json_value(item, f"{path}.{key}")
        return out
    raise TypeError(f"{path} is not JSON-serializable")


def validate_json_object(value: object, path: str = "$") -> dict[str, object]:
    result = validate_json_value(value, path)
    if not isinstance(result, dict):
        raise TypeError(f"{path} must be a JSON object")
    return result
