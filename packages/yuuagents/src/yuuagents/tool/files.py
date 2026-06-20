"""Workspace-scoped file tools."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, ClassVar

import msgspec
import pydantic
import yuullm

from yuuagents.tool.primitives import (
    Tool,
    ToolCallTask,
    ToolContext,
    ToolDefinition,
    register_tool_type,
)


class FileToolConfig(msgspec.Struct, frozen=True):
    workspace_root: str = ""
    max_read_bytes: int = 2_000_000


class ReadParams(pydantic.BaseModel):
    path: str


class EditParams(pydantic.BaseModel):
    path: str
    old_string: str
    new_string: str


class WriteParams(pydantic.BaseModel):
    path: str
    content: str


class WorkspaceFiles:
    def __init__(self, *, workspace_root: Path, max_read_bytes: int) -> None:
        self.workspace_root = workspace_root.resolve()
        self.max_read_bytes = max_read_bytes

    @classmethod
    def from_config(cls, config: FileToolConfig) -> "WorkspaceFiles":
        if not config.workspace_root:
            raise ValueError("file tools require workspace_root")
        return cls(
            workspace_root=Path(config.workspace_root),
            max_read_bytes=config.max_read_bytes,
        )

    def resolve_path(self, raw_path: str) -> Path:
        if not raw_path:
            raise ValueError("path must not be empty")
        path = Path(raw_path)
        if path.is_absolute():
            raise ValueError("path must be relative to the workspace")
        resolved = (self.workspace_root / path).resolve()
        if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
            raise ValueError(f"path escapes workspace: {raw_path!r}")
        return resolved

    def read(self, raw_path: str) -> yuullm.ToolOutput:
        path = self.resolve_path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"file not found: {raw_path}")
        size = path.stat().st_size
        if size > self.max_read_bytes:
            raise ValueError(
                f"file is too large to read: {size} bytes > {self.max_read_bytes}"
            )
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        data = path.read_bytes()
        if mime_type.startswith("image/"):
            encoded = base64.b64encode(data).decode("ascii")
            return [
                {
                    "type": "text",
                    "text": f"Read image {raw_path} ({mime_type}, {size} bytes).",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                    },
                },
            ]
        return data.decode("utf-8")

    def edit(self, *, raw_path: str, old_string: str, new_string: str) -> str:
        if old_string == "":
            raise ValueError("old_string must not be empty")
        path = self.resolve_path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"file not found: {raw_path}")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            raise ValueError("old_string was not found")
        if count > 1:
            raise ValueError(f"old_string matched {count} times; expected exactly 1")
        path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Edited {raw_path}."

    def write(self, *, raw_path: str, content: str) -> str:
        path = self.resolve_path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {raw_path}."


class ReadTool(Tool[ReadParams, yuullm.ToolOutput]):
    config_type: ClassVar[type] = FileToolConfig

    def __init__(self, files: WorkspaceFiles) -> None:
        self._files = files

    @classmethod
    def from_startup(cls, runtime: Any, config: FileToolConfig) -> "ReadTool":
        _ = runtime
        return cls(WorkspaceFiles.from_config(config))

    @property
    def definition(self) -> ToolDefinition[ReadParams, yuullm.ToolOutput]:
        return ToolDefinition(
            name="read",
            description=(
                "Read a UTF-8 text file or image from the configured workspace. "
                "Image files are returned as multimodal image content."
            ),
            input_model=ReadParams,
            tags={"builtin", "file", "read"},
        )

    async def create_coro(
        self, task: ToolCallTask, context: ToolContext
    ) -> yuullm.ToolOutput:
        _ = context
        params = ReadParams.model_validate(task.tool_call_params.params)
        return self._files.read(params.path)

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        _ = task, reason


class EditTool(Tool[EditParams, str]):
    config_type: ClassVar[type] = FileToolConfig

    def __init__(self, files: WorkspaceFiles) -> None:
        self._files = files

    @classmethod
    def from_startup(cls, runtime: Any, config: FileToolConfig) -> "EditTool":
        _ = runtime
        return cls(WorkspaceFiles.from_config(config))

    @property
    def definition(self) -> ToolDefinition[EditParams, str]:
        return ToolDefinition(
            name="edit",
            description=(
                "Replace exactly one occurrence of old_string in a UTF-8 text "
                "file under the configured workspace."
            ),
            input_model=EditParams,
            tags={"builtin", "file", "write"},
            dangerous=True,
        )

    async def create_coro(self, task: ToolCallTask, context: ToolContext) -> str:
        _ = context
        params = EditParams.model_validate(task.tool_call_params.params)
        return self._files.edit(
            raw_path=params.path,
            old_string=params.old_string,
            new_string=params.new_string,
        )

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        _ = task, reason


class WriteTool(Tool[WriteParams, str]):
    config_type: ClassVar[type] = FileToolConfig

    def __init__(self, files: WorkspaceFiles) -> None:
        self._files = files

    @classmethod
    def from_startup(cls, runtime: Any, config: FileToolConfig) -> "WriteTool":
        _ = runtime
        return cls(WorkspaceFiles.from_config(config))

    @property
    def definition(self) -> ToolDefinition[WriteParams, str]:
        return ToolDefinition(
            name="write",
            description=(
                "Write a UTF-8 text file under the configured workspace, "
                "creating parent directories as needed."
            ),
            input_model=WriteParams,
            tags={"builtin", "file", "write"},
            dangerous=True,
        )

    async def create_coro(self, task: ToolCallTask, context: ToolContext) -> str:
        _ = context
        params = WriteParams.model_validate(task.tool_call_params.params)
        return self._files.write(raw_path=params.path, content=params.content)

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        _ = task, reason


register_tool_type("read", ReadTool)
register_tool_type("edit", EditTool)
register_tool_type("write", WriteTool)
