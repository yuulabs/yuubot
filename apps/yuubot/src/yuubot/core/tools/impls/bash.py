"""Tool factory for the yuuagents bash tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from yuuagents.tool.bash import BashTool, BashToolConfig

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool


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

    def tool_class(self) -> type[Tool[Any, Any]]:
        return BashTool
