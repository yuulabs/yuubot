"""hhsh capability — translate abbreviations via nbnhhsh API."""

from __future__ import annotations

from yuubot.capabilities import capability, text_block, ContentBlock
from yuubot.skills.hhsh.cli import guess as _guess

import httpx


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
            result = await _guess(text)
        except httpx.HTTPStatusError as e:
            return [text_block(f"查询失败: HTTP {e.response.status_code}")]
        except httpx.RequestError as e:
            return [text_block(f"查询失败: {e}")]

        if result is None:
            return [text_block("(无结果)")]
        return [text_block(result)]
