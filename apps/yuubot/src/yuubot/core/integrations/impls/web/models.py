"""Typed contracts for the built-in web integration."""

from __future__ import annotations

from typing import Annotated

import msgspec

from yuubot.core.secrets import Secret


class WebConfig(msgspec.Struct, forbid_unknown_fields=False):
    api_key: Annotated[
        Secret,
        msgspec.Meta(
            title="Tavily API key",
            description="API key used by yuubot to call Tavily search.",
        ),
    ] = msgspec.field(default_factory=lambda: Secret(""))
    tavily_base_url: Annotated[
        str,
        msgspec.Meta(
            title="Tavily base URL",
            description="Base URL for Tavily API requests.",
        ),
    ] = "https://api.tavily.com"
    timeout_s: Annotated[
        float,
        msgspec.Meta(
            title="Timeout seconds",
            description="HTTP request timeout for search, read, and download.",
        ),
    ] = 30.0
    user_agent: Annotated[
        str,
        msgspec.Meta(
            title="User agent",
            description="User-Agent header used when fetching web pages and assets.",
        ),
    ] = "yuubot/0.1"
    max_read_bytes: Annotated[
        int,
        msgspec.Meta(
            title="Maximum read bytes",
            description="Server-side maximum bytes fetched by web.read.",
        ),
    ] = 2_000_000
    max_read_chars: Annotated[
        int,
        msgspec.Meta(
            title="Maximum read characters",
            description="Server-side maximum extracted text characters returned by web.read.",
        ),
    ] = 80_000
    max_download_bytes: Annotated[
        int,
        msgspec.Meta(
            title="Maximum download bytes",
            description="Server-side maximum bytes written by web.download.",
        ),
    ] = 10_000_000


class WebCitation(msgspec.Struct):
    url: str
    canonical_url: str = ""
    title: str = ""
    source: str = "web"
    fetched_at: str = ""
    published_at: str = ""
    updated_at: str = ""


class WebSearchInput(msgspec.Struct, forbid_unknown_fields=False):
    query: str
    max_results: int = 5


class SearchResult(msgspec.Struct):
    url: str
    title: str
    snippet: str = ""
    score: float = 0.0
    citation: WebCitation = msgspec.field(default_factory=lambda: WebCitation(url=""))


class WebSearchOutput(msgspec.Struct):
    results: list[SearchResult] = msgspec.field(default_factory=list)


class WebReadInput(msgspec.Struct, forbid_unknown_fields=False):
    url: str
    max_chars: int = 12_000


class WebPage(msgspec.Struct):
    url: str
    canonical_url: str = ""
    title: str = ""
    text: str = ""
    links: list[str] = msgspec.field(default_factory=list)
    image_urls: list[str] = msgspec.field(default_factory=list)
    citation: WebCitation = msgspec.field(default_factory=lambda: WebCitation(url=""))


class WebDownloadInput(msgspec.Struct, forbid_unknown_fields=False):
    url: str
    filename: str = ""
    max_bytes: int = 0


class DownloadedFile(msgspec.Struct):
    path: str
    url: str
    content_type: str = ""
    bytes: int = 0
    sha256: str = ""
    citation: WebCitation = msgspec.field(default_factory=lambda: WebCitation(url=""))


class TavilySearchResultPayload(msgspec.Struct, forbid_unknown_fields=False):
    title: str = ""
    url: str = ""
    content: str = ""
    raw_content: str | None = None
    score: float = 0.0
    published_date: str | None = None


class TavilySearchResponse(msgspec.Struct, forbid_unknown_fields=False):
    results: list[TavilySearchResultPayload] = msgspec.field(default_factory=list)
