"""Async web facade.

Use await search(query), await read(url), and await download(url) for web search,
reader extraction, and HTTP downloads.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from pathlib import Path
from typing import Final, cast

import httpx
import msgspec

DEFAULT_BASE_URL: Final[str] = "https://api.tavily.com"
DEFAULT_JINA_BASE_URL: Final[str] = "https://r.jina.ai"


class SearchResult(msgspec.Struct, frozen=True):
    title: str
    url: str
    content: str = ""


class DownloadResult(msgspec.Struct, frozen=True):
    path: str
    url: str
    content_type: str
    bytes: int
    sha256: str


class _TavilyResultWire(msgspec.Struct, frozen=True):
    title: str = ""
    url: str = ""
    content: str = ""


class _TavilySearchResponse(msgspec.Struct, frozen=True):
    results: list[_TavilyResultWire] = msgspec.field(default_factory=list)


async def search(query: str, max_results: int = 5, integration_id: str = "") -> list[SearchResult]:
    del integration_id
    api_key = os.getenv("YEXT_WEB_TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("YEXT_WEB_TAVILY_API_KEY is required for yext.web.search")
    async with _client() as client:
        response = await client.post(
            f"{os.getenv('TAVILY_BASE_URL', DEFAULT_BASE_URL).rstrip('/')}/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
        )
        response.raise_for_status()
        body = response.json()
    parsed = msgspec.convert(body, _TavilySearchResponse)
    return [SearchResult(item.title, item.url, item.content) for item in parsed.results]


async def read(url: str, max_chars: int | None = None, integration_id: str = "") -> str:
    del integration_id
    limit = max_chars if max_chars is not None else _max_read_chars()
    errors: list[str] = []
    for backend in _read_backends():
        try:
            if backend == "jina":
                return _truncate(_require_text(await _read_with_jina(url)), limit)
            if backend == "tavily":
                return _truncate(_require_text(await _read_with_tavily_extract(url)), limit)
            if backend == "httpx":
                return _truncate(_require_text(await _read_with_httpx(url)), limit)
            errors.append(f"{backend}=unknown backend")
        except Exception as exc:
            errors.append(f"{backend}={_error_summary(exc)}")
    raise RuntimeError(f"web read failed for {url}: {'; '.join(errors)}")


async def download(url: str, filename: str = "", max_bytes: int = 0, integration_id: str = "") -> DownloadResult:
    del integration_id
    data, content_type = await _fetch(url, max_bytes or _max_download_bytes())
    downloads = Path.cwd() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    name = Path(filename).name if filename else Path(urllib.parse.urlparse(url).path).name or "download.bin"
    path = downloads / name
    path.write_bytes(data)
    return DownloadResult(str(path), url, content_type, len(data), hashlib.sha256(data).hexdigest())


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


async def _read_with_jina(url: str) -> str:
    headers = {"user-agent": _user_agent()}
    api_key = os.getenv("YEXT_WEB_JINA_API_KEY", "")
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    reader_url = f"{os.getenv('YEXT_WEB_JINA_BASE_URL', DEFAULT_JINA_BASE_URL).rstrip('/')}/{urllib.parse.quote(url, safe=':/')}"
    async with _client(timeout=_jina_timeout(), headers=headers) as client:
        response = await client.get(reader_url)
        response.raise_for_status()
    return _require_text(response.text)


async def _read_with_tavily_extract(url: str) -> str:
    api_key = os.getenv("YEXT_WEB_TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("YEXT_WEB_TAVILY_API_KEY is required for Tavily extract")
    async with _client() as client:
        response = await client.post(
            f"{os.getenv('TAVILY_BASE_URL', DEFAULT_BASE_URL).rstrip('/')}/extract",
            json={
                "api_key": api_key,
                "urls": [url],
                "extract_depth": os.getenv("YEXT_WEB_TAVILY_EXTRACT_DEPTH", "basic"),
                "format": os.getenv("YEXT_WEB_TAVILY_EXTRACT_FORMAT", "markdown"),
            },
        )
        response.raise_for_status()
        body = response.json()
    return _require_text(_tavily_extract_text(body))


async def _read_with_httpx(url: str) -> str:
    data, _ = await _fetch(url, _max_read_bytes())
    return _require_text(data.decode("utf-8", errors="replace"))


def _client(timeout: float | None = None, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=headers or {"user-agent": _user_agent()}, timeout=timeout if timeout is not None else _timeout())


def _tavily_extract_text(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    payload = cast(dict[str, object], body)
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return ""
    first = results[0]
    if not isinstance(first, dict):
        return ""
    result = cast(dict[str, object], first)
    for key in ("raw_content", "content"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _require_text(text: str) -> str:
    if not text.strip():
        raise RuntimeError("empty response body")
    return text


def _truncate(text: str, max_chars: int) -> str:
    return text[:max(max_chars, 0)]


def _error_summary(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.response.status_code} {exc.response.reason_phrase}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return str(exc) or type(exc).__name__


def _timeout() -> float:
    return float(os.getenv("YEXT_WEB_TIMEOUT_S", "30"))


def _jina_timeout() -> float:
    return float(os.getenv("YEXT_WEB_JINA_TIMEOUT_S", "30"))


def _user_agent() -> str:
    return os.getenv("YEXT_WEB_USER_AGENT", "yuubot/0.1")


def _read_backends() -> list[str]:
    raw = os.getenv("YEXT_WEB_READ_BACKENDS", "jina,tavily,httpx")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _max_read_bytes() -> int:
    return int(os.getenv("YEXT_WEB_MAX_READ_BYTES", "1048576"))


def _max_read_chars() -> int:
    return int(os.getenv("YEXT_WEB_MAX_READ_CHARS", "12000"))


def _max_download_bytes() -> int:
    return int(os.getenv("YEXT_WEB_MAX_DOWNLOAD_BYTES", "104857600"))
