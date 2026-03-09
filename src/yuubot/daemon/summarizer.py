"""Context summarizer — compress session history into a handoff note on rollover."""

import logging
from typing import Any

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是一个对话摘要助手。根据提供的对话历史，生成一份简洁的工作交接摘要，"
    "让新会话能够无缝继续当前任务。\n"
    "摘要结构：\n"
    "1. 任务目标（一句话说清楚用户想要什么）\n"
    "2. 当前进展（已完成了什么，结论是什么）\n"
    "3. 下一步（如果对话中有明确的下一步，列出来）\n"
    "不超过300字，用中文，不要废话。"
)


def extract_original_task(history: list[Any]) -> str:
    """Return text of the first user message in history (the original task)."""
    for role, items in history:
        if role == "user":
            texts = [item for item in items if isinstance(item, str)]
            return "".join(texts)[:600]
    return ""


def render_recent(history: list[Any], n: int = 4) -> str:
    """Render the last N non-system messages as readable narrative."""
    msgs = [(role, items) for role, items in history if role != "system"]
    recent = msgs[-n:]
    parts: list[str] = []

    for role, items in recent:
        if role == "assistant":
            texts: list[str] = []
            tool_names: list[str] = []
            for item in items:
                if isinstance(item, str) and item.strip():
                    texts.append(item)
                elif isinstance(item, dict) and item.get("type") == "tool_call":
                    tool_names.append(item.get("name", "?"))
            if texts:
                parts.append(f"[助手]: {''.join(texts)[:400]}")
            if tool_names:
                parts.append(f"[调用工具]: {', '.join(tool_names)}")

        elif role == "user":
            texts = [item for item in items if isinstance(item, str)]
            joined = "".join(texts)[:400]
            if joined.strip():
                parts.append(f"[用户]: {joined}")

        elif role == "tool":
            snippets: list[str] = []
            for item in items:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    content = str(item.get("content", ""))[:200]
                    snippets.append(content)
            if snippets:
                parts.append(f"[工具结果]: {' | '.join(snippets)}")

    return "\n\n".join(parts) if parts else "（无记录）"


async def summarize(history: list[Any], llm: Any) -> str:
    """Call the LLM to produce a compact handoff note from session history."""
    import yuullm

    original_task = extract_original_task(history)
    recent_narrative = render_recent(history, n=4)

    user_content = (
        f"原始任务：\n{original_task}\n\n"
        f"最近对话（最后4条消息）：\n{recent_narrative}\n\n"
        "请生成工作交接摘要。"
    )

    messages = [
        yuullm.system(_SYSTEM_PROMPT),
        yuullm.user(user_content),
    ]

    try:
        from yuullm import Response
        stream, _ = await llm.stream(messages)
        text_parts: list[str] = []
        async for item in stream:
            if isinstance(item, Response) and isinstance(item.item, str):
                text_parts.append(item.item)
        return "".join(text_parts).strip()
    except Exception:
        log.exception("Summary LLM call failed, using fallback excerpt")
        return f"（原任务摘要：{original_task[:200]}）"
