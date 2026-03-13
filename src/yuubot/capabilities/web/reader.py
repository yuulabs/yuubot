"""Web page reader — Playwright + Trafilatura, reuses login state."""

import os
from typing import cast

import click
import trafilatura
from pathlib import Path
from playwright.sync_api import ProxySettings, sync_playwright
from urllib.parse import unquote, urlsplit

from yuubot.config import load_config
from .blocklist import check_url


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

    # Playwright is sync, run in thread
    import asyncio
    text = await asyncio.to_thread(_fetch_and_extract, profile, headless, url)

    if summary and len(text) > 2000:
        text = text[:2000] + "\n\n... (截断)"

    click.echo(text)


def _fetch_and_extract(profile: str, headless: bool, url: str) -> str:
    resolved = _resolve_proxy_for_url(url)
    proxy_opt = resolved[1] if resolved else None
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            proxy=proxy_opt
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)

            title = (page.title() or "untitled").strip()
            html = page.content()
            text = trafilatura.extract(html, include_links=True)
            if not text:
                text = (page.inner_text("body") or "").strip()

            return f"# {title}\n- URL: {url}\n\n---\n\n{text or '[未能抽取正文]'}"
        finally:
            ctx.close()
