"""Web page reader — dual-path (httpx fast / Playwright fallback), layered extraction."""

import os
import re
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

import click
import httpx
import trafilatura
from playwright.sync_api import Page, ProxySettings, sync_playwright
from playwright_stealth import Stealth

from yuubot.config import load_config

from .blocklist import check_url

_stealth = Stealth()

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _get_profile(config_path: str | None) -> tuple[str, bool]:
    cfg = load_config(config_path)
    profile = cfg.web.browser_profile
    Path(profile).mkdir(parents=True, exist_ok=True)
    return profile, cfg.web.headless


def _parse_proxy_url(proxy_url: str) -> tuple[dict[str, str], str]:
    parsed = urlsplit(proxy_url)
    assert parsed.scheme, f"proxy url missing scheme: {proxy_url!r}"
    assert parsed.hostname, f"proxy url missing host: {proxy_url!r}"
    port = parsed.port
    if port is None:
        port = 80 if parsed.scheme.lower() == "http" else 443

    server = f"{parsed.scheme}://{parsed.hostname}:{port}"
    proxy: dict[str, str] = {"server": server}

    username = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password

    display = server
    if username or password:
        display = f"{parsed.scheme}://{username}:***@{parsed.hostname}:{port}"

    return proxy, display


def _resolve_proxy_for_url(url: str) -> tuple[str, dict[str, str], str] | None:
    scheme = (urlsplit(url).scheme or "").lower()
    if scheme == "https":
        env_keys = ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"]
    else:
        env_keys = ["HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"]

    for key in env_keys:
        val = (os.environ.get(key) or "").strip()
        if val:
            proxy, display = _parse_proxy_url(val)
            return key, proxy, display

    return None


# ---------------------------------------------------------------------------
# Anti-bot detection
# ---------------------------------------------------------------------------

_CHALLENGE_SIGNALS = [
    "just a moment",
    "checking your browser",
    "cf-challenge",
    "attention required",
    "access denied",
    "please verify you are a human",
    "enable javascript and cookies",
]


def _is_blocked(title: str, body_text: str) -> bool:
    """Detect anti-bot challenge pages."""
    sample = (title + " " + body_text[:500]).lower()
    return any(sig in sample for sig in _CHALLENGE_SIGNALS)


# ---------------------------------------------------------------------------
# Smart waiting
# ---------------------------------------------------------------------------


def _wait_for_stable(
    page: Page,
    *,
    poll_ms: int = 500,
    stable_rounds: int = 3,
    max_rounds: int = 20,
) -> None:
    """Wait until page content stops growing (max ~10s)."""
    prev_len = 0
    stable = 0
    for _ in range(max_rounds):
        page.wait_for_timeout(poll_ms)
        cur_len = page.evaluate("document.body.innerText.length")
        if cur_len == prev_len:
            stable += 1
            if stable >= stable_rounds:
                return
        else:
            stable = 0
            prev_len = cur_len


# ---------------------------------------------------------------------------
# Layered content extraction
# ---------------------------------------------------------------------------

_SEMANTIC_EXTRACT_JS = """
() => {
    const clone = document.cloneNode(true);
    const noise = 'nav, footer, aside, header, [role="banner"], [role="navigation"],'
        + ' [role="complementary"], .nav, .footer, .sidebar, .ad, .ads,'
        + ' .advertisement, script, style, noscript, iframe';
    clone.querySelectorAll(noise).forEach(el => el.remove());

    const containers = [
        'main', 'article', '[role="main"]',
        '.content', '#content', '.post', '.article', '.entry-content',
    ];
    for (const sel of containers) {
        const el = clone.querySelector(sel);
        if (el) {
            const text = el.innerText.trim();
            if (text.length > 100) return text;
        }
    }
    return (clone.body || clone.documentElement).innerText.trim();
}
"""


def _extract_content(page: Page, html: str) -> str:
    """Layered extraction: trafilatura -> semantic JS -> cleaned body."""
    # Layer 1: trafilatura (best for articles)
    text = trafilatura.extract(html, include_links=True, include_tables=True)
    if text and len(text) > 100:
        return text

    # Layer 2: in-page semantic extraction (removes noise, finds main content)
    text = page.evaluate(_SEMANTIC_EXTRACT_JS)
    if text and len(text) > 100:
        return text

    # Layer 3: raw body (last resort)
    return (page.inner_text("body") or "").strip()


# ---------------------------------------------------------------------------
# Path 1: lightweight httpx (no browser)
# ---------------------------------------------------------------------------

_HTTPX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _try_httpx(url: str) -> str | None:
    """Fast path: httpx + trafilatura.  Returns None on failure."""
    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _HTTPX_UA},
        ) as client:
            r = client.get(url)
            r.raise_for_status()

            ct = r.headers.get("content-type", "")
            if "html" not in ct and "text" not in ct:
                return None

            html = r.text
            text = trafilatura.extract(html, include_links=True, include_tables=True)
            if not text or len(text) < 200:
                return None

            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = m.group(1).strip() if m else "untitled"
            return f"# {title}\n- URL: {url}\n\n---\n\n{text}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Path 2: Playwright (JS rendering, login state, stealth)
# ---------------------------------------------------------------------------


def _try_playwright(profile: str, headless: bool, url: str) -> str:
    """Slow path: Playwright + stealth + smart waiting + layered extraction."""
    resolved = _resolve_proxy_for_url(url)
    proxy_opt = resolved[1] if resolved else None
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            proxy=proxy_opt,
        )
        page = ctx.new_page()
        _stealth.apply_stealth_sync(page)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            _wait_for_stable(page)

            title = (page.title() or "untitled").strip()
            body_preview = (page.inner_text("body") or "")[:500]

            if _is_blocked(title, body_preview):
                return (
                    f"# {title}\n- URL: {url}\n\n---\n\n"
                    "[该网站有反爬保护 (Cloudflare/WAF)，无法读取内容。"
                    "请尝试搜索替代来源。]"
                )

            html = page.content()
            text = _extract_content(page, html)
            return f"# {title}\n- URL: {url}\n\n---\n\n{text or '[未能抽取正文]'}"
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _fetch_and_extract(profile: str, headless: bool, url: str) -> str:
    """Dual-path reader: httpx fast path, Playwright fallback."""
    result = _try_httpx(url)
    if result:
        return result
    return _try_playwright(profile, headless, url)


def run_login(config_path: str | None) -> None:
    """Open browser for manual login (persist cookies)."""
    profile, _ = _get_profile(config_path)
    click.echo(f"浏览器 profile: {profile}")
    click.echo("请在浏览器中完成登录，然后回到终端按 Enter 结束。")
    login_url = "https://example.com"
    resolved = _resolve_proxy_for_url(login_url)
    if resolved:
        proxy_env, proxy, proxy_display = resolved
        click.echo(f"代理环境变量: {proxy_env}")
        click.echo(f"代理: {proxy_display}")
    with sync_playwright() as p:
        proxy_opt: ProxySettings | None = cast(ProxySettings, resolved[1]) if resolved else None
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            proxy=proxy_opt,
        )
        page = ctx.new_page()
        page.goto(login_url, wait_until="domcontentloaded")
        input()
        ctx.close()
    click.echo("登录态已保存。")


async def read_url(url: str, summary: bool, config_path: str | None) -> None:
    """Read a URL, extract main content, print as markdown."""
    try:
        check_url(url)
    except ValueError as e:
        click.echo(str(e))
        return

    profile, headless = _get_profile(config_path)

    import asyncio

    text = await asyncio.to_thread(_fetch_and_extract, profile, headless, url)

    if summary and len(text) > 2000:
        text = text[:2000] + "\n\n... (截断)"

    click.echo(text)
