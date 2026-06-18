"""Tool type extension contracts.

Mirrors the pattern from ``core/integrations/contracts.py``:
``ToolFactory`` ↔ ``IntegrationFactory``, ``ToolKindInfo`` ↔ ``IntegrationKindInfo``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

import msgspec

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool


class ToolFactory(Protocol):
    """Registered once at startup; provides tool type metadata and creates Tool subclasses.

    Each implementation corresponds to a single ``register_tool_type`` key
    in the yuuagents runtime.  Yuubot owns registration; yuuagents Tool
    subclasses are created through ``tool_class()`` at assembly time.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def config_schema(self) -> type | dict[str, object]:
        """JSON Schema for the tool's ``config`` dict in ``ToolConfig``.

        May return a ``msgspec.Struct`` subclass (converted to JSON Schema
        by ``tool_kind_info``) or a pre-built schema dict.
        """
        return {}

    def tool_class(self) -> type[Tool[Any, Any]]:
        """Return the yuuagents ``Tool`` subclass for assembly."""
        ...


@dataclass
class ToolKindInfo:
    """Static metadata about a registered tool type.

    The admin UI uses this to render a selection list for ``agent_tools``
    configuration.
    """

    name: str
    description: str = ""
    config_schema: dict[str, object] = field(default_factory=dict)


def tool_kind_info(factory: ToolFactory) -> ToolKindInfo:
    """Project a factory to its admin-facing kind descriptor."""
    config_type = factory.config_schema
    schema: dict[str, object] = {}
    if isinstance(config_type, type) and issubclass(config_type, msgspec.Struct):
        schema = _inline_root_schema(msgspec.json.schema(config_type))
    elif isinstance(config_type, dict):
        schema = dict(config_type)
    return ToolKindInfo(
        name=factory.name,
        description=factory.description,
        config_schema=schema,
    )


def _inline_root_schema(schema: dict[str, object]) -> dict[str, object]:
    """Inline the top-level $ref so callers see a self-contained object schema."""
    ref = schema.get("$ref")
    defs = schema.get("$defs")
    if not isinstance(ref, str) or not isinstance(defs, dict):
        return schema
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return schema
    root_name = ref[len(prefix):]
    defs_map = cast(dict[str, object], defs)
    root = defs_map.get(root_name)
    if not isinstance(root, dict):
        return schema
    inlined = dict(cast(dict[str, object], root))
    remaining = {k: v for k, v in defs_map.items() if k != root_name}
    if remaining:
        inlined["$defs"] = remaining
    return inlined
