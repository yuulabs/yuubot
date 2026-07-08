from pathlib import Path
from typing import cast

import msgspec

from .base import workspace_tool
from .paths import workspace_path

DESCRIPTION = """Apply an exact string replacement in a workspace text file.

`old_string` must be non-empty and must match exactly once in the target file. Zero matches or multiple matches are errors; the file is not modified in those cases. This prevents ambiguous or speculative edits.

`new_string` may be empty to delete the matched text. Paths are relative to the workspace root and cannot escape the workspace boundary.

Use this for precise, reviewable edits to existing files. Use `write` to create new files or replace an entire file at once."""


class EditPayload(msgspec.Struct, frozen=True):
    path: str
    old_string: str
    new_string: str


async def _execute_edit(root: Path, payload: msgspec.Struct) -> str:
    data = cast(EditPayload, payload)
    if not data.old_string:
        raise ValueError("old_string cannot be empty")
    path = workspace_path(root, data.path)
    text = path.read_text(encoding="utf-8")
    count = text.count(data.old_string)
    if count != 1:
        raise ValueError(f"old_string must match exactly once, got {count}")
    path.write_text(text.replace(data.old_string, data.new_string), encoding="utf-8")
    return f"edited {data.path}"


EDIT_SPEC = workspace_tool(EditPayload, DESCRIPTION, _execute_edit)
