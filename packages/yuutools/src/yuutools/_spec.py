"""Tool specification and schema generation."""

from __future__ import annotations

import json
from typing import Any

import msgspec


class ParamSpec(msgspec.Struct, frozen=True):
    """Schema for a single parameter."""

    name: str
    type_schema: dict[str, Any]
    description: str
    required: bool = True


class ToolSpec(msgspec.Struct, frozen=True):
    """Immutable specification of a tool's callable interface.

    Holds the parameter schemas (excluding dependency-injected params)
    and can serialize to JSON Schema or YAML.
    """

    name: str
    description: str
    params: tuple[ParamSpec, ...]

    def to(self, fmt: str) -> str:
        """Serialize the spec.

        Parameters
        ----------
        fmt:
            ``"json_schema"`` or ``"yaml"``.
        """
        schema = self._to_json_schema()
        if fmt == "json_schema":
            return json.dumps(schema, indent=2, ensure_ascii=False)
        if fmt == "yaml":
            try:
                import yaml
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError(
                    "pyyaml is required for YAML output: "
                    "pip install yuutools[yaml]"
                ) from exc
            return yaml.dump(schema, default_flow_style=False, allow_unicode=True)
        raise ValueError(f"unsupported format: {fmt!r}")

    # -- internals ----------------------------------------------------------

    def _to_json_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            prop: dict[str, Any] = dict(p.type_schema)
            if p.description:
                prop["description"] = p.description
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        schema: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        if required:
            schema["function"]["parameters"]["required"] = required
        return schema
