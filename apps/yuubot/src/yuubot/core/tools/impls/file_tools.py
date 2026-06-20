"""Tool factories for yuuagents workspace file tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from yuuagents.tool.files import EditTool, FileToolConfig, ReadTool, WriteTool

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool


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

    def tool_class(self) -> type[Tool[Any, Any]]:
        return WriteTool
