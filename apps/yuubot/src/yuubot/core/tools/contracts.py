"""Tool type extension contracts.

Mirrors the pattern from ``core/integrations/contracts.py``:
``ToolFactory`` ↔ ``IntegrationFactory``, ``ToolKindInfo`` ↔ ``IntegrationKindInfo``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

import msgspec

if TYPE_CHECKING:
    from yuubot.core.assembly._compiler import ToolDeriveContext
    from yuuagents.tool.primitives import Tool


class EmptyFrontendFields(msgspec.Struct):
    """A tool whose full runtime config is derived from context (§3.5).

    The user fills in no fields — every config field is produced by the
    system ``ToolFactory.derive`` from the assembly context.
    """


class ToolFactory(Protocol):
    """Registered once at startup; provides tool type metadata and creates Tool subclasses.

    Each implementation corresponds to a single ``register_tool_type`` key
    in the yuuagents runtime.  Yuubot owns registration; yuuagents Tool
    subclasses are created through ``tool_class()`` at assembly time.

    The system (yuubot) owns per-tool derivation: ``derive`` converts a
    ``ToolSelection.user_fields`` dict + the assembly ``ToolDeriveContext``
    into a fully typed runtime config (``config_schema`` instance). Tool
    classes (yuuagents) do not participate in derivation.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def config_schema(self) -> type | dict[str, object]:
        """JSON Schema for the tool's ``config`` dict.

        May return a ``msgspec.Struct`` subclass (converted to JSON Schema
        by ``tool_kind_info``) or a pre-built schema dict.
        """
        return {}

    @property
    def user_fields_type(self) -> type[msgspec.Struct]:
        """System-defined frontend-fields schema for this tool (§3.5).

        A ``msgspec.Struct`` subclass whose JSON Schema drives the Admin UI
        form for ``ToolSelection.user_fields``. Defaults to
        ``EmptyFrontendFields`` when every config field is derived from
        context.
        """
        return EmptyFrontendFields

    def derive(
        self,
        user_fields: dict[str, object],
        context: "ToolDeriveContext",
    ) -> msgspec.Struct:
        """System-layer derivation: user_fields + context → typed config.

        Returns an instance of ``config_schema``. The compiler system calls
        this exactly once per ``ToolSelection`` at assembly time (§3.6).
        """
        ...

    def tool_class(self) -> type[Tool[Any, Any]]:
        """Return the yuuagents ``Tool`` subclass for assembly."""
        ...


@dataclass
class ToolKindInfo:
    """Static metadata about a registered tool type.

    The admin UI uses this to render a selection list for the CapabilitySet
    ``tools`` editor.
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
