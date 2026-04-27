"""Controlled workspace functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def read_file(path: str) -> str:
    """Read a file under the current controlled workspace."""
    return await _p.call("workspace", "read_file", path=path)


async def write_file(path: str, content: str) -> dict[str, Any]:
    """Write a file under the current controlled workspace."""
    return await _p.call("workspace", "write_file", path=path, content=content)


async def list_files(path: str = ".") -> list[dict[str, Any]]:
    """List files under the current controlled workspace."""
    return await _p.call("workspace", "list_files", path=path)


async def apply_patch(patch: str) -> dict[str, Any]:
    """Apply a patch within the current controlled workspace."""
    return await _p.call("workspace", "apply_patch", patch=patch)
