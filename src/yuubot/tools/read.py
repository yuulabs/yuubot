import mimetypes
from pathlib import Path
from typing import Final, cast

import msgspec

from ..domain.messages import ContentItem
from .base import workspace_tool
from .paths import workspace_path

MAX_READ_LINES: Final[int] = 2_000
MAX_READ_BYTES: Final[int] = 1024 * 1024
VISION_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)

DESCRIPTION = """Read a file inside the actor workspace.

Paths are relative to the workspace root and cannot escape the workspace boundary.

Text files are decoded as UTF-8 with replacement characters for invalid bytes. Use `start_lo` (0-based line index) and `end_lo` (exclusive line index, or -1 for end of file) to page through large files. Full-file reads return at most 2,000 complete lines and 1 MiB; when output is truncated or paged, the result includes the final line position. The byte limit is applied on a complete-line boundary whenever possible, so the tool does not normally cut a line in half.

PNG, JPEG, WEBP, and non-animated GIF files are handled differently: when the current model supports vision, the result contains image content for multimodal inspection. SVG files are read as text/XML. Other image formats return a clear conversion message instead of image bytes.

Use this tool to inspect workspace files, skill documents, AGENTS.md, and generated artifacts. Prefer `execute_python` for multi-step data processing."""


class ReadPayload(msgspec.Struct, frozen=True):
    path: str
    start_lo: int = 0
    end_lo: int = -1


async def _execute_read(root: Path, payload: msgspec.Struct, model: str, supports_vision: bool) -> str | list[ContentItem]:
    data = cast(ReadPayload, payload)
    path = workspace_path(root, data.path)
    mime, _ = mimetypes.guess_type(path)
    # SVG is an image media type, but it is also a text/XML source file. Let it
    # fall through to the text reader. Only pass formats accepted by the
    # provider's vision input contract as image content.
    if (mime or "").startswith("image/") and mime != "image/svg+xml":
        if mime not in VISION_MIME_TYPES:
            rel_path = path.relative_to(root).as_posix()
            return (
                f"{rel_path} uses unsupported image format {mime or 'unknown'}; "
                "convert it to PNG, JPEG, WEBP, or non-animated GIF first."
            )
        rel_path = path.relative_to(root).as_posix()
        if not supports_vision:
            return f"{rel_path} is an image, but model {model} does not support vision."
        return [
            ContentItem("image", path=rel_path, mime=mime or "image/*"),
        ]

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(data.start_lo, 0)
    end = len(lines) if data.end_lo < 0 else min(data.end_lo, len(lines))
    start = min(start, end)
    selected = lines[start : min(end, start + MAX_READ_LINES)]
    truncated_by_lines = end > start + len(selected)
    text = "\n".join(selected)
    if len(text.encode("utf-8")) > MAX_READ_BYTES:
        fitting: list[str] = []
        size = 0
        for line in selected:
            line_size = len(line.encode("utf-8")) + (1 if fitting else 0)
            if fitting and size + line_size > MAX_READ_BYTES:
                break
            if not fitting and line_size > MAX_READ_BYTES:
                # A single unusually long line cannot be represented without
                # splitting it; keep a UTF-8-safe prefix as the last resort.
                text = line.encode("utf-8")[:MAX_READ_BYTES].decode("utf-8", errors="ignore")
                fitting = [text]
                break
            fitting.append(line)
            size += line_size
        truncated_by_bytes = len(fitting) < len(selected) or (bool(fitting) and fitting[0] != selected[0])
        selected = fitting
        text = "\n".join(selected)
    else:
        truncated_by_bytes = False
    final_lo = start + len(selected)
    if truncated_by_lines or truncated_by_bytes or start > 0 or end < len(lines):
        text += f"\n[truncated: lines {start}-{final_lo} of {len(lines)}]"
    return text


READ_SPEC = workspace_tool(
    ReadPayload,
    DESCRIPTION,
    _execute_read,
    lambda context: {"model": context.model, "supports_vision": context.model_supports_vision},
)
