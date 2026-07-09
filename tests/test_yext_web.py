from __future__ import annotations

import pytest

import yext.web as web


@pytest.mark.asyncio
async def test_read_returns_jina_result_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def read_jina(url: str) -> str:
        calls.append(f"jina:{url}")
        return "jina page"

    async def read_tavily(url: str) -> str:
        calls.append(f"tavily:{url}")
        return "tavily page"

    monkeypatch.setenv("YEXT_WEB_READ_BACKENDS", "jina,tavily,httpx")
    monkeypatch.setattr(web, "_read_with_jina", read_jina)
    monkeypatch.setattr(web, "_read_with_tavily_extract", read_tavily)

    assert await web.read("https://example.com") == "jina page"
    assert calls == ["jina:https://example.com"]


@pytest.mark.asyncio
async def test_read_falls_back_from_jina_to_tavily(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def read_jina(url: str) -> str:
        calls.append("jina")
        raise RuntimeError("403 Forbidden")

    async def read_tavily(url: str) -> str:
        calls.append("tavily")
        return "tavily page"

    async def read_httpx(url: str) -> str:
        calls.append("httpx")
        return "httpx page"

    monkeypatch.setenv("YEXT_WEB_READ_BACKENDS", "jina,tavily,httpx")
    monkeypatch.setattr(web, "_read_with_jina", read_jina)
    monkeypatch.setattr(web, "_read_with_tavily_extract", read_tavily)
    monkeypatch.setattr(web, "_read_with_httpx", read_httpx)

    assert await web.read("https://example.com") == "tavily page"
    assert calls == ["jina", "tavily"]


@pytest.mark.asyncio
async def test_read_falls_back_to_httpx_after_jina_and_tavily_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def read_jina(url: str) -> str:
        calls.append("jina")
        raise RuntimeError("empty response body")

    async def read_tavily(url: str) -> str:
        calls.append("tavily")
        raise RuntimeError("429 Too Many Requests")

    async def read_httpx(url: str) -> str:
        calls.append("httpx")
        return "httpx page"

    monkeypatch.setenv("YEXT_WEB_READ_BACKENDS", "jina,tavily,httpx")
    monkeypatch.setattr(web, "_read_with_jina", read_jina)
    monkeypatch.setattr(web, "_read_with_tavily_extract", read_tavily)
    monkeypatch.setattr(web, "_read_with_httpx", read_httpx)

    assert await web.read("https://example.com") == "httpx page"
    assert calls == ["jina", "tavily", "httpx"]


@pytest.mark.asyncio
async def test_read_reports_all_backend_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read_jina(url: str) -> str:
        raise RuntimeError("403 Forbidden")

    async def read_tavily(url: str) -> str:
        raise RuntimeError("429 Too Many Requests")

    async def read_httpx(url: str) -> str:
        raise RuntimeError("403 Forbidden")

    monkeypatch.setenv("YEXT_WEB_READ_BACKENDS", "jina,tavily,httpx")
    monkeypatch.setattr(web, "_read_with_jina", read_jina)
    monkeypatch.setattr(web, "_read_with_tavily_extract", read_tavily)
    monkeypatch.setattr(web, "_read_with_httpx", read_httpx)

    with pytest.raises(RuntimeError) as exc_info:
        await web.read("https://example.com")

    message = str(exc_info.value)
    assert "jina=403 Forbidden" in message
    assert "tavily=429 Too Many Requests" in message
    assert "httpx=403 Forbidden" in message


def test_tavily_extract_text_accepts_raw_content() -> None:
    body = {"results": [{"raw_content": "markdown", "content": "fallback"}]}

    assert web._tavily_extract_text(body) == "markdown"
