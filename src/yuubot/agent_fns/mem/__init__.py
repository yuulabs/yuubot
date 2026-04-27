"""Memory functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def recall_memory(query: str, *, limit: int = 5, scope: str | None = None) -> list[dict[str, Any]]:
    """Recall memories relevant to a query within the current Master/Group scope."""
    return await _p.call("mem", "recall", query=query, limit=limit, scope=scope or "")


async def save_memory(content: str, *, tags: list[str] | None = None, scope: str = "private") -> dict[str, Any]:
    """Save a memory with tags and scope derived from the current context."""
    return await _p.call("mem", "save", content=content, tags=tags or [], scope=scope)


async def archive_memory(memory_id: int) -> dict[str, Any]:
    """Archive or soft-delete a memory entry."""
    return await _p.call("mem", "archive", memory_id=memory_id)


async def restore_memory(memory_id: int) -> dict[str, Any]:
    """Restore a soft-deleted memory entry."""
    return await _p.call("mem", "restore", memory_id=memory_id)
