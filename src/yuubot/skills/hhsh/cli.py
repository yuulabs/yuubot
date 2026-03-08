"""hhsh (能不能好好说话) — translate abbreviations via nbnhhsh API."""

import click
import httpx

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
        if items:
            lines.append(f"{name}: {' / '.join(items)}")
        else:
            lines.append(f"{name}: (无结果)")

    return "\n".join(lines) if lines else None


async def run_guess(text: str) -> None:
    """CLI entry: guess abbreviation and print result."""
    try:
        result = await guess(text)
    except httpx.HTTPStatusError as e:
        click.echo(f"查询失败: HTTP {e.response.status_code}")
        raise SystemExit(1)
    except httpx.RequestError as e:
        click.echo(f"查询失败: {e}")
        raise SystemExit(1)

    if result is None:
        click.echo("(无结果)")
    else:
        click.echo(result)
