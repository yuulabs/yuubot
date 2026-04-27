"""Web search, page reading, and controlled download service."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit

import attrs
import httpx
import trafilatura

from yuubot.config import Config
from yuubot.services.base import InvalidScope, YuubotServiceError


_SEARCH_RATE_DIR = Path("/tmp/yuubot_rate")
_SEARCH_LIMIT = 3
_HTTPX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_BLOCKED_DOMAINS = {
    "ipinfo.io",
    "ip-api.com",
    "ifconfig.me",
    "ifconfig.co",
    "icanhazip.com",
    "whatismyip.com",
    "myip.com",
    "ipify.org",
    "api.ipify.org",
    "checkip.amazonaws.com",
    "ipgeolocation.io",
    "ip.sb",
    "ipapi.co",
    "freegeoip.app",
    "geojs.io",
    "wtfismyip.com",
    "httpbin.org",
}


def _is_bot_mode(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("agent_name") or payload.get("character_name") or os.environ.get("YUU_IN_BOT"))


def _check_url(url: str, payload: Mapping[str, Any]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidScope("only http(s) URLs are allowed")
    if not _is_bot_mode(payload):
        return
    hostname = (parsed.hostname or "").lower()
    for domain in _BLOCKED_DOMAINS:
        if hostname == domain or hostname.endswith("." + domain):
            raise InvalidScope(f"安全策略: 禁止访问IP/地理位置查询服务 ({domain})")


def _check_search_quota(task_id: str) -> tuple[bool, int]:
    if not task_id:
        return True, -1
    _SEARCH_RATE_DIR.mkdir(parents=True, exist_ok=True)
    counter_file = _SEARCH_RATE_DIR / f"web_search_{task_id}"
    try:
        count = int(counter_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        count = 0
    if count >= _SEARCH_LIMIT:
        return False, 0
    count += 1
    counter_file.write_text(str(count), encoding="utf-8")
    return True, _SEARCH_LIMIT - count


def _absolutize_urls(text: str, base_url: str) -> str:
    def replace_img(m: re.Match) -> str:
        alt, url = m.group(1), m.group(2)
        if not url.startswith(("http://", "https://", "//", "data:")):
            url = urljoin(base_url, url)
        return f"![{alt}]({url})"

    def replace_link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if not url.startswith(("http://", "https://", "//", "#", "mailto:", "data:")):
            url = urljoin(base_url, url)
        return f"[{label}]({url})"

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_img, text)
    text = re.sub(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)", replace_link, text)
    return text


def _extract_title(html: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return fallback
    return re.sub(r"\s+", " ", match.group(1)).strip() or fallback


def _safe_filename(url: str, fallback: str = "download") -> str:
    parsed = urlparse(url)
    name = os.path.basename(parsed.path) or fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or fallback
    return name


@attrs.define
class WebService:
    config: Config | None = None

    async def search(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = str(payload.get("query", "") or "").strip()
        if not query:
            return []
        allowed, remaining = _check_search_quota(str(payload.get("task_id", "") or ""))
        if not allowed:
            raise YuubotServiceError(f"本次任务搜索次数已达上限 ({_SEARCH_LIMIT}/{_SEARCH_LIMIT})")
        limit = max(1, min(_int(payload.get("limit")) or 5, 10))
        api_key = ""
        if self.config is not None:
            api_key = str(self.config.api_keys.get("tavily", "") or "")
        api_key = str(payload.get("tavily_api_key", "") or api_key)
        if not api_key:
            raise YuubotServiceError("未配置 Tavily API key")

        request = {
            "api_key": api_key,
            "query": query,
            "max_results": limit,
            "include_answer": True,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://api.tavily.com/search", json=request)
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("results", [])[:limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title", "") or ""),
                    "url": str(item.get("url", "") or ""),
                    "content": str(item.get("content", "") or ""),
                    "score": item.get("score"),
                }
            )
        if remaining >= 0:
            for item in results:
                item["remaining_search_quota"] = remaining
        return results

    async def read_page(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url", "") or "").strip()
        if not url:
            raise YuubotServiceError("url is required")
        _check_url(url, payload)
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": _HTTPX_UA},
        ) as client:
            response = await client.get(url)
        response.raise_for_status()
        final_url = str(response.url)
        content_type = response.headers.get("content-type", "")
        html = response.text
        title = _extract_title(html, url)
        extracted = ""
        if "html" in content_type or "<html" in html[:500].lower():
            extracted = trafilatura.extract(html, include_links=True, include_tables=True, include_images=True) or ""
        if not extracted:
            extracted = html
        extracted = _absolutize_urls(extracted, final_url)
        page_size = max(500, min(_int(payload.get("page_size")) or 5000, 50000))
        page = max(0, _int(payload.get("page")) or 0)
        full_size = len(extracted)
        page_count = max(1, math.ceil(full_size / page_size))
        page = min(page, page_count - 1)
        start = page * page_size
        page_text = extracted[start : start + page_size]
        return {
            "title": title,
            "url": final_url,
            "content_type": content_type,
            "full_size": full_size,
            "page_size": page_size,
            "page_count": page_count,
            "page": page,
            "has_more": page < page_count - 1,
            "text": page_text,
            "references": [{"title": title, "url": final_url, "source": "web"}],
        }

    async def download(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url", "") or "").strip()
        if not url:
            raise YuubotServiceError("url is required")
        _check_url(url, payload)
        raw_workspace = str(payload.get("workspace_root", "") or "")
        if raw_workspace:
            workspace = Path(raw_workspace).expanduser()
        else:
            if self.config is None:
                raise InvalidScope("workspace_root is unavailable")
            workspace = Path(self.config.web.download_dir).expanduser()
        workspace.mkdir(parents=True, exist_ok=True)
        filename = str(payload.get("filename", "") or _safe_filename(url))
        filename = _safe_filename(filename)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(url)
        response.raise_for_status()
        if "." not in filename:
            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                filename += ".html"
            elif "json" in content_type:
                filename += ".json"
        out_path = _unique_path(workspace / filename)
        if workspace.resolve() not in (out_path.resolve(), *out_path.resolve().parents):
            raise InvalidScope("download path escaped workspace")
        out_path.write_bytes(response.content)
        return {
            "status": "downloaded",
            "url": str(response.url),
            "path": str(out_path),
            "bytes": len(response.content),
            "content_type": response.headers.get("content-type", ""),
        }


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _int(value: object) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return 0
    return 0
