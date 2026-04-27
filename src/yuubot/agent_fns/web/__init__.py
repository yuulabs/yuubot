"""Web research functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def web_search(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Search the web and return structured results with citation metadata."""
    return await _p.call("web", "search", query=query, limit=limit)


async def read_page(url: str, *, page: int = 0, page_size: int = 5000) -> dict[str, Any]:
    """Read a page. Returns paginated text with full_size/page_count/has_more metadata."""
    return await _p.call("web", "read_page", url=url, page=page, page_size=page_size)


async def download_url(url: str, *, filename: str | None = None) -> dict[str, Any]:
    """Download a URL into the controlled yuubot workspace/cache."""
    return await _p.call("web", "download", url=url, filename=filename or "")
