"""Async HTTP client and HTML extraction for the built-in web integration."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
import msgspec

from yuubot.core.integrations.impls.web.models import (
    DownloadedFile,
    SearchResult,
    TavilySearchResponse,
    WebCitation,
    WebPage,
)

_SKIP_TEXT_TAGS = {"script", "style", "noscript", "template", "svg"}
_BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._/-]+")


@dataclass
class WebClient:
    http: httpx.AsyncClient
    tavily_base_url: str
    tavily_api_key: str
    max_read_bytes: int
    max_read_chars: int
    max_download_bytes: int

    @classmethod
    def from_config(
        cls,
        *,
        api_key: str,
        tavily_base_url: str,
        timeout_s: float,
        user_agent: str,
        max_read_bytes: int,
        max_read_chars: int,
        max_download_bytes: int,
    ) -> "WebClient":
        return cls(
            http=httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s),
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            ),
            tavily_base_url=tavily_base_url.rstrip("/"),
            tavily_api_key=api_key,
            max_read_bytes=max_read_bytes,
            max_read_chars=max_read_chars,
            max_download_bytes=max_download_bytes,
        )

    async def search(self, *, query: str, max_results: int) -> list[SearchResult]:
        if not self.tavily_api_key:
            raise ValueError("web integration is not connected")
        response = await self.http.post(
            f"{self.tavily_base_url}/search",
            headers={"Authorization": f"Bearer {self.tavily_api_key}"},
            json={
                "query": query,
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        response.raise_for_status()
        payload = msgspec.convert(
            response.json(),
            type=TavilySearchResponse,
            strict=False,
        )
        fetched_at = _utc_now()
        return [
            SearchResult(
                url=item.url,
                title=item.title,
                snippet=item.content or item.raw_content or "",
                score=item.score,
                citation=WebCitation(
                    url=item.url,
                    canonical_url=item.url,
                    title=item.title,
                    source="tavily",
                    fetched_at=fetched_at,
                    published_at=item.published_date or "",
                ),
            )
            for item in payload.results
        ]

    async def read(self, *, url: str, max_chars: int) -> WebPage:
        response = await self._get_limited(url, max_bytes=self.max_read_bytes)
        content_type = response.headers.get("content-type", "")
        text = response.content.decode(response.encoding or "utf-8", errors="replace")
        page = extract_page(
            url=str(response.url),
            html=text,
            content_type=content_type,
            fetched_at=_utc_now(),
            max_chars=min(max_chars, self.max_read_chars),
        )
        return page

    async def download(
        self,
        *,
        url: str,
        workspace_path: Path,
        filename: str,
        max_bytes: int,
    ) -> DownloadedFile:
        effective_limit = self.max_download_bytes
        if max_bytes > 0:
            effective_limit = min(max_bytes, self.max_download_bytes)
        response = await self._get_limited(url, max_bytes=effective_limit)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        target = resolve_download_path(
            workspace_path=workspace_path,
            url=str(response.url),
            filename=filename,
            content_type=content_type,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        digest = hashlib.sha256(response.content).hexdigest()
        relative_path = target.relative_to(workspace_path.resolve()).as_posix()
        return DownloadedFile(
            path=relative_path,
            url=str(response.url),
            content_type=content_type,
            bytes=len(response.content),
            sha256=digest,
            citation=WebCitation(
                url=url,
                canonical_url=str(response.url),
                source="web",
                fetched_at=_utc_now(),
            ),
        )

    async def _get_limited(self, url: str, *, max_bytes: int) -> httpx.Response:
        chunks: list[bytes] = []
        total = 0
        async with self.http.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"response exceeds limit: {total} bytes > {max_bytes}")
                chunks.append(chunk)
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                request=response.request,
                extensions=response.extensions,
            )

    async def close(self) -> None:
        await self.http.aclose()


def extract_page(
    *,
    url: str,
    html: str,
    content_type: str,
    fetched_at: str,
    max_chars: int,
) -> WebPage:
    if "html" not in content_type.lower():
        text = _limit_text(html, max_chars=max_chars)
        return WebPage(
            url=url,
            canonical_url=url,
            text=text,
            citation=WebCitation(url=url, canonical_url=url, fetched_at=fetched_at),
        )

    parser = _WebHtmlParser(base_url=url)
    parser.feed(html)
    parser.close()
    canonical_url = parser.canonical_url or url
    text = _limit_text(parser.text(), max_chars=max_chars)
    return WebPage(
        url=url,
        canonical_url=canonical_url,
        title=parser.title.strip(),
        text=text,
        links=_dedupe(parser.links),
        image_urls=_dedupe(parser.image_urls),
        citation=WebCitation(
            url=url,
            canonical_url=canonical_url,
            title=parser.title.strip(),
            source="web",
            fetched_at=fetched_at,
            published_at=parser.published_at,
            updated_at=parser.updated_at,
        ),
    )


def resolve_download_path(
    *,
    workspace_path: Path,
    url: str,
    filename: str,
    content_type: str,
) -> Path:
    workspace = workspace_path.resolve()
    base = (workspace / "downloads" / "web").resolve()
    relative_name = filename.strip() or _filename_from_url(url)
    if not relative_name:
        relative_name = "download"
    relative_name = _sanitize_relative_name(relative_name)
    target = (base / relative_name).resolve()
    if target != base and base not in target.parents:
        raise ValueError("download filename escapes downloads/web")
    target = _with_inferred_suffix(target, url=url, content_type=content_type)
    if target != base and base not in target.parents:
        raise ValueError("download filename escapes downloads/web")
    return target


class _WebHtmlParser(HTMLParser):
    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.canonical_url = ""
        self.published_at = ""
        self.updated_at = ""
        self.links: list[str] = []
        self.image_urls: list[str] = []
        self._tag_stack: list[str] = []
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        self._tag_stack.append(normalized)
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if normalized == "a" and attr_map.get("href"):
            self.links.append(urljoin(self.base_url, attr_map["href"]))
        if normalized == "img":
            self._collect_image_attrs(attr_map)
        if normalized == "source":
            self._collect_srcset(attr_map.get("srcset", ""))
        if normalized == "link" and "canonical" in attr_map.get("rel", "").lower():
            self.canonical_url = urljoin(self.base_url, attr_map.get("href", ""))
        if normalized == "meta":
            self._collect_meta(attr_map)
        if normalized in _BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        while self._tag_stack:
            current = self._tag_stack.pop()
            if current == normalized:
                break
        if normalized == "title" and not self.title:
            self.title = " ".join("".join(self._title_parts).split())
        if normalized in _BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if any(tag in _SKIP_TEXT_TAGS for tag in self._tag_stack):
            return
        if self._tag_stack and self._tag_stack[-1] == "title":
            self._title_parts.append(data)
            return
        value = " ".join(unescape(data).split())
        if value:
            self._text_parts.append(value)
            self._text_parts.append(" ")

    def text(self) -> str:
        return _normalize_text("".join(self._text_parts))

    def _collect_image_attrs(self, attrs: dict[str, str]) -> None:
        src = attrs.get("src", "")
        if src:
            self.image_urls.append(urljoin(self.base_url, src))
        self._collect_srcset(attrs.get("srcset", ""))

    def _collect_srcset(self, srcset: str) -> None:
        for part in srcset.split(","):
            url_part = part.strip().split(" ", 1)[0]
            if url_part:
                self.image_urls.append(urljoin(self.base_url, url_part))

    def _collect_meta(self, attrs: dict[str, str]) -> None:
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content", "")
        if not content:
            return
        if key in {"og:title", "twitter:title"} and not self.title:
            self.title = content
        if key in {"og:image", "twitter:image"}:
            self.image_urls.append(urljoin(self.base_url, content))
        if key in {"article:published_time", "date", "pubdate"}:
            self.published_at = content
        if key in {"article:modified_time", "last-modified", "updated_time"}:
            self.updated_at = content


def _sanitize_relative_name(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        raise ValueError("download filename must be relative")
    cleaned = _SAFE_NAME_RE.sub("_", value.replace("\\", "/")).strip("/")
    parts = [part for part in cleaned.split("/") if part not in {"", ".", ".."}]
    if not parts:
        return "download"
    if len(parts) != len([part for part in cleaned.split("/") if part]):
        raise ValueError("download filename must not contain traversal segments")
    return posixpath.join(*parts)


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(posixpath.basename(parsed.path)).strip()
    return name or "download"


def _with_inferred_suffix(target: Path, *, url: str, content_type: str) -> Path:
    if target.suffix:
        return target
    url_suffix = Path(urlparse(url).path).suffix
    if url_suffix:
        return target.with_suffix(url_suffix)
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed:
        return target.with_suffix(guessed)
    return target


def _normalize_text(value: str) -> str:
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line).strip()


def _limit_text(value: str, *, max_chars: int) -> str:
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    if len(value) <= max_chars:
        return value
    return value[:max_chars]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
