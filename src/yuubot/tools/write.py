from pathlib import Path
from typing import cast

import msgspec

from .base import workspace_tool
from .paths import workspace_path

DESCRIPTION = """Write UTF-8 text to a file inside the actor workspace.

Parent directories are created automatically. Paths are relative to the workspace root and cannot escape the workspace boundary. Existing file content is replaced.

Use `artifacts/` for user-visible outputs, `notes/` for durable actor notes, and `projects/` for longer-lived project files. Use `edit` when you only need to change part of an existing file."""


class WritePayload(msgspec.Struct, frozen=True):
    path: str
    content: str


async def _execute_write(root: Path, payload: msgspec.Struct) -> str:
    data = cast(WritePayload, payload)
    path = workspace_path(root, data.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data.content, encoding="utf-8")
    return f"wrote {data.path}"


WRITE_SPEC = workspace_tool(WritePayload, DESCRIPTION, _execute_write)
