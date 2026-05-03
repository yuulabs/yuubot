"""Web research functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import TypedDict, cast

from yuubot.agent_fns.local import local_config, service_payload
from yuubot.services.web import WebService


__all__ = ["web_search", "read_page", "download_url"]


class WebSearchResult(TypedDict, total=False):
    title: str
    url: str
    content: str
    score: float | int | None
    remaining_search_quota: int


class WebReference(TypedDict):
    title: str
    url: str
    source: str


class ReadPageResult(TypedDict):
    title: str
    url: str
    content_type: str
    full_size: int
    page_size: int
    page_count: int
    page: int
    has_more: bool
    text: str
    references: list[WebReference]


class DownloadResult(TypedDict):
    status: str
    url: str
    path: str
    bytes: int
    content_type: str


async def web_search(query: str, *, limit: int = 5) -> list[WebSearchResult]:
    """Search the web for query and return dict results.

    Each item is a dict: use result["title"], result["url"], result["content"],
    result["score"], and optional result["remaining_search_quota"]. Do not use
    attribute access like result.title.
    """
    return cast(
        list[WebSearchResult],
        await WebService(config=local_config()).search(service_payload(query=query, limit=limit)),
    )


async def read_page(url: str, *, page: int = 0, page_size: int = 5000) -> ReadPageResult:
    """Fetch a URL and return extracted paginated text plus title, final url, page counts, and references."""
    return cast(
        ReadPageResult,
        await WebService(config=local_config()).read_page(
            service_payload(url=url, page=page, page_size=page_size)
        ),
    )


async def download_url(url: str, *, filename: str | None = None) -> DownloadResult:
    """Download a URL into the workspace/cache and return saved path, bytes, content type, and final url."""
    return cast(
        DownloadResult,
        await WebService(config=local_config()).download(service_payload(url=url, filename=filename or "")),
    )
