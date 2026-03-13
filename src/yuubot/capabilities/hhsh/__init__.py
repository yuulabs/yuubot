"""hhsh capability — translate abbreviations via nbnhhsh API."""

from __future__ import annotations

import httpx

from yuubot.capabilities import ContentBlock, capability, text_block

HHSH_URL = "https://lab.magiconch.com/api/nbnhhsh/guess"


async def guess(text: str) -> str | None:
    """Query nbnhhsh API. Returns formatted result or None."""
    q = text.strip()
    if not q:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(HHSH_URL, json={"text": q})
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, list) or not data:
        return None

    lines: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        trans = item.get("trans") or item.get("inputting")
        if not trans:
            lines.append(f"{name}: (无结果)")
            continue
        items = [str(x) for x in trans][:10] if isinstance(trans, list) else [str(trans)]
        items = [x for x in items if x]
        lines.append(f"{name}: {' / '.join(items)}" if items else f"{name}: (无结果)")

    return "\n".join(lines) if lines else None


async def run_guess(text: str) -> None:
    """CLI entry: guess abbreviation and print result."""
    import click

    try:
        result = await guess(text)
    except httpx.HTTPStatusError as e:
        click.echo(f"查询失败: HTTP {e.response.status_code}")
        raise SystemExit(1)
    except httpx.RequestError as e:
        click.echo(f"查询失败: {e}")
        raise SystemExit(1)

    click.echo(result or "(无结果)")


@capability("hhsh")
class HhshCapability:
    async def guess(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        text = " ".join(_positional) if _positional else ""
        if not text:
            return [text_block("错误: 请提供缩写文本")]

        try:
            result = await guess(text)
        except httpx.HTTPStatusError as e:
            return [text_block(f"查询失败: HTTP {e.response.status_code}")]
        except httpx.RequestError as e:
            return [text_block(f"查询失败: {e}")]

        if result is None:
            return [text_block("(无结果)")]
        return [text_block(result)]
