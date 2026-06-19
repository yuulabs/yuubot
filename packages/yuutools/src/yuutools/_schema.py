"""Python type annotation -> JSON Schema mapping."""

from __future__ import annotations

import types
import typing
from typing import Any, Union, get_args, get_origin


def _is_optional(tp: Any) -> tuple[bool, Any]:
    """Return (True, inner) if tp is Optional[inner], else (False, tp)."""
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = get_args(tp)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            return True, non_none[0]
    return False, tp


def type_to_json_schema(tp: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    if tp is type(None):
        return {"type": "null"}

    optional, inner = _is_optional(tp)
    if optional:
        schema = type_to_json_schema(inner)
        return {"anyOf": [schema, {"type": "null"}]}

    origin = get_origin(tp)

    # list[X]
    if origin is list:
        args = get_args(tp)
        if args:
            return {"type": "array", "items": type_to_json_schema(args[0])}
        return {"type": "array"}

    # dict[K, V]
    if origin is dict:
        args = get_args(tp)
        schema: dict[str, Any] = {"type": "object"}
        if args and len(args) == 2:
            schema["additionalProperties"] = type_to_json_schema(args[1])
        return schema

    # tuple[X, Y, ...]
    if origin is tuple:
        args = get_args(tp)
        if args:
            return {
                "type": "array",
                "prefixItems": [type_to_json_schema(a) for a in args],
                "minItems": len(args),
                "maxItems": len(args),
            }
        return {"type": "array"}

    # Union[X, Y] (non-Optional)
    if origin is Union or origin is types.UnionType:
        args = get_args(tp)
        return {"anyOf": [type_to_json_schema(a) for a in args]}

    # Literal
    if origin is typing.Literal:
        args = get_args(tp)
        return {"enum": list(args)}

    # primitives
    _PRIMITIVES: dict[type, str] = {
        int: "integer",
        float: "number",
        str: "string",
        bool: "boolean",
    }
    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}

    # fallback
    return {"type": "string"}
