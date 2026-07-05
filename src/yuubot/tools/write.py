from pathlib import Path
from typing import ClassVar, cast

import msgspec
from attrs import define

from ..domain.messages import ConversationContext
from ..runtime.core import Runtime
from .base import ToolConfig, ToolSpec
from .paths import workspace, workspace_path

DESCRIPTION = """Write UTF-8 text to a file inside the actor workspace.

Parent directories are created automatically. Paths are relative to the workspace root and cannot escape the workspace boundary. Existing file content is replaced.

Use `artifacts/` for user-visible outputs, `notes/` for durable actor notes, and `projects/` for longer-lived project files. Use `edit` when you only need to change part of an existing file."""


class WritePayload(msgspec.Struct, frozen=True, kw_only=True):
    path: str
    content: str


@define
class WriteTool:
    payload_type: ClassVar[type[msgspec.Struct]] = WritePayload

    workspace: Path

    async def execute(self, payload: msgspec.Struct) -> str:
        data = cast(WritePayload, payload)
        path = workspace_path(self.workspace, data.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data.content, encoding="utf-8")
        return f"wrote {data.path}"

    async def close(self) -> None:
        return None


def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> WriteTool:
    del config, runtime
    return WriteTool(workspace=workspace(context.workspace))


WRITE_SPEC = ToolSpec(payload_type=WritePayload, description=DESCRIPTION, factory=_factory)
