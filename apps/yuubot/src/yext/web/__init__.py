"""Hand-written web facade for actor Python sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from yuubot.core.facade.protocol import FacadeRpcRequest
from yb import _context
from yb._client import request as _request


@dataclass(frozen=True)
class Citation:
    url: str
    canonical_url: str = ""
    title: str = ""
    source: str = "web"
    fetched_at: str = ""
    published_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "Citation":
        return cls(
            url=str(payload.get("url", "")),
            canonical_url=str(payload.get("canonical_url", "")),
            title=str(payload.get("title", "")),
            source=str(payload.get("source", "web")),
            fetched_at=str(payload.get("fetched_at", "")),
            published_at=str(payload.get("published_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str = ""
    score: float = 0.0
    citation: Citation = Citation(url="")

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SearchResult":
        return cls(
            url=str(payload["url"]),
            title=str(payload.get("title", "")),
            snippet=str(payload.get("snippet", "")),
            score=_float_field(payload.get("score", 0.0)),
            citation=Citation.from_payload(_object_field(payload, "citation")),
        )

    def __str__(self) -> str:
        snippet = _brief(self.snippet)
        return f"{self.title} [{self.url}]" + (f" - {snippet}" if snippet else "")

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
            "citation": self.citation.as_dict(),
        }


@dataclass(frozen=True)
class WebPage:
    url: str
    canonical_url: str
    title: str
    text: str
    links: list[str]
    image_urls: list[str]
    citation: Citation

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "WebPage":
        return cls(
            url=str(payload["url"]),
            canonical_url=str(payload.get("canonical_url", "")),
            title=str(payload.get("title", "")),
            text=str(payload.get("text", "")),
            links=_string_list(payload.get("links", [])),
            image_urls=_string_list(payload.get("image_urls", [])),
            citation=Citation.from_payload(_object_field(payload, "citation")),
        )

    def __str__(self) -> str:
        return (
            f"{self.title or self.url} "
            f"[text_chars={len(self.text)}; links={len(self.links)}; "
            f"images={len(self.image_urls)}]"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "text_chars": len(self.text),
            "links": list(self.links),
            "image_urls": list(self.image_urls),
            "citation": self.citation.as_dict(),
        }


@dataclass(frozen=True)
class DownloadedFile:
    path: str
    url: str
    content_type: str = ""
    bytes: int = 0
    sha256: str = ""
    citation: Citation = Citation(url="")

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "DownloadedFile":
        return cls(
            path=str(payload["path"]),
            url=str(payload["url"]),
            content_type=str(payload.get("content_type", "")),
            bytes=_int_field(payload.get("bytes", 0)),
            sha256=str(payload.get("sha256", "")),
            citation=Citation.from_payload(_object_field(payload, "citation")),
        )

    def __str__(self) -> str:
        return f"{self.path} [{self.content_type}; {self.bytes} bytes]"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "url": self.url,
            "content_type": self.content_type,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "citation": self.citation.as_dict(),
        }


async def search(
    query: str,
    *,
    max_results: int = 5,
    integration_id: str = "",
) -> list[SearchResult]:
    result = await _invoke(
        "web.search",
        {"query": query, "max_results": max_results},
        integration_id=integration_id,
    )
    items = result.get("results", [])
    if not isinstance(items, list):
        raise TypeError("web.search result must contain a results list")
    return [SearchResult.from_payload(_require_object(item)) for item in items]


async def read(
    url: str,
    *,
    max_chars: int = 12_000,
    integration_id: str = "",
) -> WebPage:
    result = await _invoke(
        "web.read",
        {"url": url, "max_chars": max_chars},
        integration_id=integration_id,
    )
    return WebPage.from_payload(result)


async def download(
    url: str,
    *,
    filename: str = "",
    max_bytes: int = 0,
    integration_id: str = "",
) -> DownloadedFile:
    result = await _invoke(
        "web.download",
        {"url": url, "filename": filename, "max_bytes": max_bytes},
        integration_id=integration_id,
    )
    return DownloadedFile.from_payload(result)


async def _invoke(
    capability_id: str,
    payload: dict[str, object],
    *,
    integration_id: str = "",
) -> dict[str, object]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    response = await _request(
        FacadeRpcRequest(
            token=bridge.token,
            actor_id=actor.actor_id,
            integration_id=integration_id,
            agent_name=actor.agent_name,
            session_id=actor.session_id,
            mailbox_id=actor.mailbox_id,
            capability_id=capability_id,
            payload=payload,
        )
    )
    if not response.ok:
        error = response.error
        msg = error.message if error else "unknown facade error"
        raise RuntimeError(f"capability {capability_id!r} failed: {msg}")
    result = response.result
    if not isinstance(result, dict):
        raise TypeError("web facade result must be a JSON object")
    return result


def _require_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("web facade expected a JSON object")
    return cast(dict[str, object], value)


def _object_field(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise TypeError(f"web facade expected object field {key!r}")
    return cast(dict[str, object], value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("web facade expected a list")
    return [str(item) for item in value]


def _float_field(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    raise TypeError("web facade expected a number")


def _int_field(value: object) -> int:
    if isinstance(value, int):
        return value
    raise TypeError("web facade expected an integer")


def _brief(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= 160:
        return compact
    return compact[:160]


__all__ = [
    "Citation",
    "DownloadedFile",
    "SearchResult",
    "WebPage",
    "download",
    "read",
    "search",
]
