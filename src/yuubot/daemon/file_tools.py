"""File-access tools for RFC2 agents.

Three tools are provided:
- ``read_file``      — read any file by path (master agents only)
- ``edit_file``      — exact-string replacement in a text file (master agents only)
- ``read_chat_file`` — read a media file that appeared in the current chat context
                       (group agents; path must exist in message media_files records)

Image files return a ``yuullm.ImageItem`` (vision content) when the session's
model supports vision, otherwise a text prompt to use a Python vision helper.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yuullm
from attrs import define
from yuuagents.tools import ToolRunContext, ToolSpecContext


_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_MAX_TEXT_BYTES = 128 * 1024  # 128 KB


def _resolve_path(raw: str) -> Path:
    """Strip file:// prefix and return a Path."""
    if raw.startswith("file://"):
        raw = raw[7:]  # leaves leading / intact for absolute paths
    return Path(raw)


def _detect_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _supports_vision(context: ToolRunContext) -> bool:
    state = context.runtime.python.state if context.runtime.python else {}
    return bool(state.get("supports_vision", False))


def _read_output(path: Path, *, supports_vision: bool) -> yuullm.ToolOutput:
    """Core read logic shared by all three tools."""
    if not path.exists():
        return f"文件不存在: {path}"

    mime = _detect_mime(path)

    if mime in _IMAGE_MIMES:
        if supports_vision:
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode()
            return [yuullm.ImageItem(type="image_url", image_url={"url": f"data:{mime};base64,{b64}"})]
        return (
            f"[图片文件: {path.name}] 当前模型不支持视觉，"
            "请在 execute_python 中调用可用的 "
            f"`yb.describe_image({str(path)!r})` 或 `vision.describe_image({str(path)!r})` "
            f"获取图片描述。\npath: {path}"
        )

    # Try text
    try:
        raw = path.read_bytes()
        truncated = ""
        if len(raw) > _MAX_TEXT_BYTES:
            raw = raw[:_MAX_TEXT_BYTES]
            truncated = f"\n... [已截断，文件总大小 {path.stat().st_size} 字节]"
        return raw.decode("utf-8", errors="replace") + truncated
    except Exception as exc:
        return f"读取失败 ({mime}): {exc}"


# ── ReadFileTool ───────────────────────────────────────────────────────────────


@define
class ReadFileTool:
    """Read any file by absolute path. For master (kernel) agents only."""

    name: str = "read_file"

    def spec(self, context: ToolSpecContext) -> dict[str, Any]:
        del context
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a file by absolute path. "
                    "Text files return their content (truncated at 128 KB). "
                    "Image files return vision content when the model supports vision; "
                    "otherwise returns a prompt to use a Python vision helper."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute filesystem path or file:/// URL.",
                        }
                    },
                    "required": ["path"],
                },
            },
        }

    async def run(self, context: ToolRunContext, arguments: Mapping[str, Any]) -> yuullm.ToolOutput:
        try:
            path = _resolve_path(str(arguments.get("path", "")))
            return _read_output(path, supports_vision=_supports_vision(context))
        except Exception as exc:
            return f"read_file 失败: {exc}"


# ── EditFileTool ───────────────────────────────────────────────────────────────


@define
class EditFileTool:
    """Exact-string replacement in a text file. For master (kernel) agents only."""

    name: str = "edit_file"

    def spec(self, context: ToolSpecContext) -> dict[str, Any]:
        del context
        return {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Edit a text file by replacing an exact string. "
                    "Fails if old_str is not found or matches more than once "
                    "(provide more context to make it unique)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute filesystem path to the file.",
                        },
                        "old_str": {
                            "type": "string",
                            "description": "Exact string to replace. Must appear exactly once.",
                        },
                        "new_str": {
                            "type": "string",
                            "description": "Replacement string.",
                        },
                    },
                    "required": ["path", "old_str", "new_str"],
                },
            },
        }

    async def run(self, context: ToolRunContext, arguments: Mapping[str, Any]) -> yuullm.ToolOutput:
        del context
        try:
            path = _resolve_path(str(arguments.get("path", "")))
            old_str = str(arguments.get("old_str", ""))
            new_str = str(arguments.get("new_str", ""))

            if not path.exists():
                return f"文件不存在: {path}"
            if not old_str:
                return "old_str 不能为空"

            content = path.read_text(encoding="utf-8")
            count = content.count(old_str)
            if count == 0:
                return "未找到匹配字符串，文件未修改。"
            if count > 1:
                return f"找到 {count} 处匹配，需唯一匹配才能安全替换，请提供更多上下文。"

            path.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")
            return f"已替换，文件已保存: {path}"
        except Exception as exc:
            return f"edit_file 失败: {exc}"


# ── ReadChatFileTool ───────────────────────────────────────────────────────────


@define
class ReadChatFileTool:
    """Read a media file that appeared in the current chat context.

    Path must be present in ``media_files`` of messages for the current ctx_id.
    This restriction lets group agents access QQ-delivered images without
    granting general filesystem access.
    """

    name: str = "read_chat_file"

    def spec(self, context: ToolSpecContext) -> dict[str, Any]:
        del context
        return {
            "type": "function",
            "function": {
                "name": "read_chat_file",
                "description": (
                    "Read an image or file that was sent in the current chat. "
                    "Only paths from <img src> tags in rendered messages are allowed. "
                    "Image files return vision content when the model supports vision; "
                    "otherwise returns a prompt to use a Python vision helper."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path from an <img src> tag in a rendered message.",
                        }
                    },
                    "required": ["path"],
                },
            },
        }

    async def run(self, context: ToolRunContext, arguments: Mapping[str, Any]) -> yuullm.ToolOutput:
        try:
            state = context.runtime.python.state if context.runtime.python else {}
            ctx_id = int(state.get("ctx_id", 0))

            path = _resolve_path(str(arguments.get("path", "")))
            allowed = await _chat_media_paths(ctx_id)
            if str(path) not in allowed:
                return f"路径不在当前会话的消息记录中，拒绝访问: {path}"

            return _read_output(path, supports_vision=_supports_vision(context))
        except Exception as exc:
            return f"read_chat_file 失败: {exc}"


async def _chat_media_paths(ctx_id: int) -> frozenset[str]:
    """Return the set of normalized local paths from media_files for the given ctx."""
    from tortoise import connections

    conn = connections.get("default")
    _, rows = await conn.execute_query(
        "SELECT media_files FROM messages WHERE ctx_id = ? AND media_files IS NOT NULL "
        "ORDER BY id DESC LIMIT 500",
        [ctx_id],
    )
    paths: set[str] = set()
    for row in rows:
        raw = str(row[0] or "")
        try:
            items: list[object] = json.loads(raw) if raw.startswith("[") else [raw]
        except Exception:
            continue
        for item in items:
            p = str(item).strip()
            if p.startswith("file://"):
                p = p[7:]
            if p.startswith("/"):
                paths.add(p)
    return frozenset(paths)
