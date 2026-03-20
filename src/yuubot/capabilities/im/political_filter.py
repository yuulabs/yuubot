"""Political content filter — block politically sensitive content in bot mode."""

import os
import re

# ---------------------------------------------------------------------------
# Keyword lists by category
# ---------------------------------------------------------------------------

# 现任/近届领导人
_LEADERS: list[str] = [
    "习近平", "李强", "赵乐际", "王沪宁", "蔡奇", "丁薛祥", "李希", "韩正",
    "胡锦涛", "温家宝", "江泽民", "李克强",
    "习主席", "习总书记", "习大大",
]

# 政党/机构
_PARTIES: list[str] = [
    "共产党", "国民党", "民进党",
    "中共", "中央政治局", "政治局常委",
]

# 敏感政治术语
_SENSITIVE_TERMS: list[str] = [
    "六四", "天安门事件", "文化大革命", "文革",
    "法轮功", "台独", "藏独", "疆独",
]

_ALL_KEYWORDS: list[str] = _LEADERS + _PARTIES + _SENSITIVE_TERMS

# Pre-compiled pattern for efficient matching
_PATTERN: re.Pattern[str] = re.compile("|".join(re.escape(k) for k in _ALL_KEYWORDS))


def _is_bot_mode() -> bool:
    return os.environ.get("YUU_IN_BOT", "").lower() in ("1", "true", "yes")


def check_political_content(text: str) -> str | None:
    """Return the matched keyword if text contains political content, else None.

    Only active when YUU_IN_BOT=1.
    """
    if not _is_bot_mode():
        return None
    m = _PATTERN.search(text)
    return m.group(0) if m else None
