"""Async web facade.

Use await search(query), await read(url), and await download(url) for Tavily-backed search and HTTP reads.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from pathlib import Path
from typing import Final

import httpx
import msgspec

DEFAULT_BASE_URL: Final[str] = "https://api.tavily.com"


class SearchResult(msgspec.Struct, frozen=True, kw_only=True):
    title: str
    url: str
    content: str = ""


class DownloadResult(msgspec.Struct, frozen=True, kw_only=True):
    path: str
    url: str
    content_type: str
    bytes: int
    sha256: str


async def search(query: str, *, max_results: int = 5, integration_id: str = "") -> list[SearchResult]:
    del integration_id
    api_key = os.getenv("TAVILY_API_KEY") or os.getenv("YEXT_WEB_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is required for yext.web.search")
    async with _client() as client:
        response = await client.post(
            f"{os.getenv('TAVILY_BASE_URL', DEFAULT_BASE_URL).rstrip('/')}/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
        )
        response.raise_for_status()
        body = response.json()
    results = body.get("results", []) if isinstance(body, dict) else []
    return [
        SearchResult(title=str(item.get("title", "")), url=str(item.get("url", "")), content=str(item.get("content", "")))
        for item in results
        if isinstance(item, dict)
    ]


async def read(url: str, *, max_chars: int | None = None, integration_id: str = "") -> str:
    del integration_id
    data, _ = await _fetch(url, _max_read_bytes())
    return data.decode("utf-8", errors="replace")[: max_chars if max_chars is not None else _max_read_chars()]


async def download(url: str, *, filename: str = "", max_bytes: int = 0, integration_id: str = "") -> DownloadResult:
    del integration_id
    data, content_type = await _fetch(url, max_bytes or _max_download_bytes())
    downloads = Path.cwd() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    name = Path(filename).name if filename else Path(urllib.parse.urlparse(url).path).name or "download.bin"
    path = downloads / name
    path.write_bytes(data)
    return DownloadResult(path=str(path), url=url, content_type=content_type, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


async def _fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    remaining = max(max_bytes, 0)
    async with _client() as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                remaining -= len(chunks[-1])
            content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    return b"".join(chunks), content_type


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"user-agent": _user_agent()}, timeout=_timeout())


def _timeout() -> float:
    return float(os.getenv("YEXT_WEB_TIMEOUT_S", "30"))


def _user_agent() -> str:
    return os.getenv("YEXT_WEB_USER_AGENT", "yuubot/0.1")


def _max_read_bytes() -> int:
    return int(os.getenv("YEXT_WEB_MAX_READ_BYTES", "1048576"))


def _max_read_chars() -> int:
    return int(os.getenv("YEXT_WEB_MAX_READ_CHARS", "12000"))


def _max_download_bytes() -> int:
    return int(os.getenv("YEXT_WEB_MAX_DOWNLOAD_BYTES", "104857600"))
