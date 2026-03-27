from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from yuubot.capabilities.web.reader import _fetch_and_extract


def test_fetch_and_extract_returns_friendly_message_when_playwright_times_out(monkeypatch):
    url = "https://www.youtube.com/watch?v=6ErM97TA1Sk"

    monkeypatch.setattr("yuubot.capabilities.web.reader._try_httpx", lambda _: None)

    def fake_try_playwright(profile: str, headless: bool, target_url: str) -> str:
        raise PlaywrightTimeoutError("Page.goto: Timeout 60000ms exceeded.")

    monkeypatch.setattr("yuubot.capabilities.web.reader._try_playwright", fake_try_playwright)

    text = _fetch_and_extract("/tmp/profile", True, url)

    assert "页面加载超时" in text
    assert url in text


def test_fetch_and_extract_returns_friendly_message_when_playwright_crashes(monkeypatch):
    url = "https://example.com"

    monkeypatch.setattr("yuubot.capabilities.web.reader._try_httpx", lambda _: None)

    def fake_try_playwright(profile: str, headless: bool, target_url: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("yuubot.capabilities.web.reader._try_playwright", fake_try_playwright)

    text = _fetch_and_extract("/tmp/profile", True, url)

    assert "浏览器抓取失败" in text
    assert "RuntimeError: boom" in text
    assert url in text
