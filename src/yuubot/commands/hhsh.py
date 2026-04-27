"""hhsh command — translate abbreviations via nbnhhsh API."""

from __future__ import annotations

import httpx

from yuubot.commands.tree import CommandRequest


HHSH_URL = "https://lab.magiconch.com/api/nbnhhsh/guess"


async def guess(text: str) -> str | None:
    """Query nbnhhsh API and format the response for QQ."""
    query = text.strip()
    if not query:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(HHSH_URL, json={"text": query})
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list) or not data:
        return None

    lines: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or query)
        translations = item.get("trans") or item.get("inputting")
        if not translations:
            lines.append(f"{name}: (无结果)")
            continue

        if isinstance(translations, list):
            values = [str(value) for value in translations[:10] if value]
        else:
            values = [str(translations)]

        lines.append(f"{name}: {' / '.join(values)}" if values else f"{name}: (无结果)")

    return "\n".join(lines) if lines else None


async def exec_hhsh(request: CommandRequest) -> str | None:
    """Translate abbreviation: /hhsh <text>."""
    text = request.remaining.strip()
    if not text:
        return "用法: /hhsh <缩写>，例如: /hhsh yyds"

    try:
        result = await guess(text)
    except httpx.HTTPStatusError as exc:
        return f"hhsh 查询失败: HTTP {exc.response.status_code}"
    except httpx.RequestError:
        return "hhsh 查询失败"

    return result or "(无结果)"
