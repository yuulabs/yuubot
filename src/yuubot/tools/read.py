import mimetypes
from pathlib import Path
from typing import Final, cast

import msgspec

from ..domain.messages import ContentItem, ModelCard
from .base import workspace_tool
from .paths import workspace_path

MAX_READ_LINES: Final[int] = 300
MAX_READ_BYTES: Final[int] = 64 * 1024

DESCRIPTION = """Read a file inside the actor workspace.

Paths are relative to the workspace root and cannot escape the workspace boundary.

Text files are decoded as UTF-8 with replacement characters for invalid bytes. Use `start_lo` (0-based line index) and `end_lo` (exclusive line index, or -1 for end of file) to page through large files. Full-file reads are capped at 300 lines and 64 KiB; when output is truncated, the result includes the final line position.

Image files are handled differently: when the current model supports vision, the result contains image content for multimodal inspection. When the model does not support vision, the tool returns a clear text message instead of image bytes.

Use this tool to inspect workspace files, skill documents, AGENTS.md, and generated artifacts. Prefer `execute_python` for multi-step data processing."""


class ReadPayload(msgspec.Struct, frozen=True):
    path: str
    start_lo: int = 0
    end_lo: int = -1


async def _execute_read(root: Path, payload: msgspec.Struct, model: ModelCard) -> str | list[ContentItem]:
    data = cast(ReadPayload, payload)
    path = workspace_path(root, data.path)
    mime, _ = mimetypes.guess_type(path)
    if (mime or "").startswith("image/"):
        rel_path = path.relative_to(root).as_posix()
        if not model.vision:
            return f"{rel_path} is an image, but model {model.selector} does not support vision."
        return [
            ContentItem("image", path=rel_path, mime=mime or "image/*"),
        ]

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(data.start_lo, 0)
    end = len(lines) if data.end_lo < 0 else min(data.end_lo, len(lines))
    start = min(start, end)
    selected = lines[start:end]
    truncated_by_lines = len(selected) > MAX_READ_LINES
    if truncated_by_lines:
        selected = selected[:MAX_READ_LINES]
    text = "\n".join(selected)
    raw = text.encode("utf-8")
    truncated_by_bytes = len(raw) > MAX_READ_BYTES
    if truncated_by_bytes:
        text = raw[:MAX_READ_BYTES].decode("utf-8", errors="ignore")
    final_lo = start + len(selected)
    if truncated_by_lines or truncated_by_bytes or start > 0 or end < len(lines):
        text += f"\n[truncated: lines {start}-{final_lo} of {len(lines)}]"
    return text


READ_SPEC = workspace_tool(
    ReadPayload,
    DESCRIPTION,
    _execute_read,
    lambda context: {"model": context.model},
)
