from __future__ import annotations

from pathlib import Path

import pytest

import yext.web as web


@pytest.fixture(autouse=True)
def clear_read_cache() -> None:
    web._read_cache.clear()


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
async def test_read_caches_successful_extraction_and_applies_each_call_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def read_jina(url: str) -> str:
        nonlocal calls
        calls += 1
        return "cached page"

    monkeypatch.setenv("YEXT_WEB_READ_BACKENDS", "jina")
    monkeypatch.setattr(web, "_read_with_jina", read_jina)

    assert await web.read("https://example.com", max_chars=6) == (
        "cached\n[truncated: characters 0-6 of 11; omitted characters 6-11]"
    )
    assert await web.read("https://example.com", max_chars=11) == "cached page"
    assert calls == 1


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


@pytest.mark.asyncio
async def test_download_saves_relative_filename_under_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
        assert url == "https://example.com/logo.png"
        return b"image", "image/png"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web, "_fetch", fetch)

    result = await web.download("https://example.com/logo.png", "artifacts/shiori-game-images/logo.png")

    assert result.path == str(tmp_path / "artifacts" / "shiori-game-images" / "logo.png")
    assert (tmp_path / "artifacts" / "shiori-game-images" / "logo.png").read_bytes() == b"image"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["../outside.png", "/tmp/outside.png"])
async def test_download_rejects_filename_outside_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, filename: str
) -> None:
    async def fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
        pytest.fail("invalid output path must fail before downloading")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web, "_fetch", fetch)

    with pytest.raises(ValueError, match="inside the workspace|stay inside the workspace"):
        await web.download("https://example.com/logo.png", filename)
