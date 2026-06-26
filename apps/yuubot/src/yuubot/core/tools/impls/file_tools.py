"""Tool factories for yuuagents workspace file tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import msgspec
from yuuagents.tool.files import EditTool, FileToolConfig, ReadTool, WriteTool

from yuubot.core.tools.contracts import EmptyFrontendFields

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool
    from yuubot.core.assembly._compiler import ToolDeriveContext


class ReadToolFactory:
    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Read text files and images from the configured workspace."

    @property
    def config_schema(self) -> type[FileToolConfig]:
        return FileToolConfig

    @property
    def user_fields_type(self) -> type[msgspec.Struct]:
        return EmptyFrontendFields

    def derive(
        self,
        user_fields: dict[str, object],
        context: "ToolDeriveContext",
    ) -> FileToolConfig:
        # workspace_root ← context.workspace_path; max_read_bytes default (§6.2).
        return FileToolConfig(workspace_root=context.workspace_path)

    def tool_class(self) -> type[Tool[Any, Any]]:
        return ReadTool


class EditToolFactory:
    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "Edit a workspace text file by exact string replacement."

    @property
    def config_schema(self) -> type[FileToolConfig]:
        return FileToolConfig

    @property
    def user_fields_type(self) -> type[msgspec.Struct]:
        return EmptyFrontendFields

    def derive(
        self,
        user_fields: dict[str, object],
        context: "ToolDeriveContext",
    ) -> FileToolConfig:
        # workspace_root ← context.workspace_path (§6.3).
        return FileToolConfig(workspace_root=context.workspace_path)

    def tool_class(self) -> type[Tool[Any, Any]]:
        return EditTool


class WriteToolFactory:
    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Write text files under the configured workspace."

    @property
    def config_schema(self) -> type[FileToolConfig]:
        return FileToolConfig

    @property
    def user_fields_type(self) -> type[msgspec.Struct]:
        return EmptyFrontendFields

    def derive(
        self,
        user_fields: dict[str, object],
        context: "ToolDeriveContext",
    ) -> FileToolConfig:
        # workspace_root ← context.workspace_path (§6.4).
        return FileToolConfig(workspace_root=context.workspace_path)

    def tool_class(self) -> type[Tool[Any, Any]]:
        return WriteTool
