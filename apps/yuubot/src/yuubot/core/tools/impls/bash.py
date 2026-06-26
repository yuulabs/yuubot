"""Tool factory for the yuuagents bash tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import msgspec
from yuuagents.tool.bash import BashTool, BashToolConfig

from yuubot.core.tools.contracts import EmptyFrontendFields

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool
    from yuubot.core.assembly._compiler import ToolDeriveContext


class BashToolFactory:
    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Run one initialized bash command in the configured workspace."

    @property
    def config_schema(self) -> type[BashToolConfig]:
        return BashToolConfig

    @property
    def user_fields_type(self) -> type[msgspec.Struct]:
        return EmptyFrontendFields

    def derive(
        self,
        user_fields: dict[str, object],
        context: "ToolDeriveContext",
    ) -> BashToolConfig:
        # workspace_root ← context.workspace_path; safety/timeout fields
        # keep their struct defaults (§6.1).
        return BashToolConfig(workspace_root=context.workspace_path)

    def tool_class(self) -> type[Tool[Any, Any]]:
        return BashTool
