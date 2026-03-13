"""Web capability — search, read, download."""

from __future__ import annotations

from yuubot.capabilities import capability, get_context, text_block, ContentBlock
from yuubot.config import load_config


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


@capability("web")
class WebCapability:

    async def search(
        self,
        *,
        _positional: list[str] | None = None,
        limit: int = 5,
        **_kw,
    ) -> list[ContentBlock]:
        query = " ".join(_positional) if _positional else ""
        if not query:
            return [text_block("错误: 请提供搜索关键词")]

        from .search import _check_search_quota

        allowed, remaining = _check_search_quota()
        if not allowed:
            return [text_block("错误: 本次任务搜索次数已达上限 (3/3)。停止搜索，基于已有信息回答或告知用户无法获取。")]

        cfg = _get_config()
        api_key = cfg.api_keys.get("tavily", "")
        if not api_key:
            return [text_block("错误: 未配置 TAVILY_API_KEY")]

        import httpx

        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": limit,
            "include_answer": True,
        }
        data = None
        for no_proxy in (False, True):
            kwargs: dict = {"timeout": 30}
            if no_proxy:
                kwargs["trust_env"] = False
            try:
                async with httpx.AsyncClient(**kwargs) as client:
                    r = await client.post("https://api.tavily.com/search", json=payload)
                    r.raise_for_status()
                    data = r.json()
                    break
            except httpx.ConnectError:
                if no_proxy:
                    raise
        assert data is not None

        results = data.get("results", [])
        if not results:
            msg = "搜索无结果。这可能意味着：1) 信息不在网上 2) 关键词不准确。不要反复尝试相似搜索。"
            if remaining >= 0:
                msg += f" (剩余额度: {remaining}/3)"
            return [text_block(msg)]

        lines = []
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("content", "")[:200]
            lines.append(f"{i}. [{title}] {url}")
            lines.append(f"   {snippet}")
            lines.append("")

        if remaining >= 0:
            lines.append(f"(剩余搜索额度: {remaining}/3)")

        return [text_block("\n".join(lines))]

    async def read(
        self,
        *,
        _positional: list[str] | None = None,
        summary: bool = False,
        **_kw,
    ) -> list[ContentBlock]:
        url = _positional[0] if _positional else ""
        if not url:
            return [text_block("错误: 请提供 URL")]

        from .blocklist import check_url
        try:
            check_url(url)
        except ValueError as e:
            return [text_block(str(e))]

        from .reader import _fetch_and_extract, _get_profile
        import asyncio

        profile, headless = _get_profile(None)
        text = await asyncio.to_thread(_fetch_and_extract, profile, headless, url)

        if summary and len(text) > 2000:
            text = text[:2000] + "\n\n... (截断)"

        return [text_block(text)]

    async def download(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        if not _positional or len(_positional) < 2:
            return [text_block("错误: 用法: web download <urls> <folder>")]

        folder = _positional[-1]
        urls_str = "\n".join(_positional[:-1])

        from .blocklist import check_url
        from pathlib import Path
        import httpx

        urls = [u.strip() for u in urls_str.strip().splitlines() if u.strip()]
        if not urls:
            return [text_block("没有提供 URL")]

        dest = Path(folder)
        dest.mkdir(parents=True, exist_ok=True)

        lines = []
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for url in urls:
                try:
                    check_url(url)
                except ValueError as e:
                    lines.append(f"[x] {url} — {e}")
                    continue
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    from urllib.parse import urlparse
                    import os
                    parsed = urlparse(url)
                    fname = os.path.basename(parsed.path) or "download"
                    if not Path(fname).suffix:
                        ct = r.headers.get("content-type", "")
                        if "html" in ct:
                            fname += ".html"
                        elif "json" in ct:
                            fname += ".json"
                    out_path = dest / fname
                    counter = 1
                    while out_path.exists():
                        stem = Path(fname).stem
                        suffix = Path(fname).suffix
                        out_path = dest / f"{stem}_{counter}{suffix}"
                        counter += 1
                    out_path.write_bytes(r.content)
                    lines.append(f"[ok] {url} -> {out_path}")
                except Exception as e:
                    lines.append(f"[x] {url} — {e}")

        return [text_block("\n".join(lines))]
