"""Mid-step context compressor — compresses agent history when input tokens exceed threshold."""

from typing import Awaitable, Callable

import attrs
from loguru import logger


@attrs.define
class SessionCompressor:
    """Implements yuuagents ContextCompressor protocol.

    When input_tokens exceeds max_tokens, compresses history by:
    1. Keeping system prompt (index 0)
    2. Extracting tool usage stats from compressed portion
    3. LLM-summarizing the compressed portion (looking back summarize_steps_span steps)
    4. Preserving the last step's messages (assistant + all its tool results/pings)
    """

    max_tokens: int
    summarize_fn: Callable[[list, int], Awaitable[str]]
    summarize_steps_span: int = 8

    async def compress(self, history: list, input_tokens: int) -> list | None:
        if input_tokens < self.max_tokens:
            return None

        if len(history) < 3:
            return None

        system_msg = history[0]
        rest = history[1:]

        # Preserve the last step (last assistant + all subsequent tool/user messages)
        last_step = _extract_last_step(rest)
        to_compress = rest[: -len(last_step)] if last_step else rest
        if not to_compress:
            return None

        tool_stats = _extract_tool_stats(to_compress)

        try:
            summary = await self.summarize_fn(to_compress, self.summarize_steps_span)
        except Exception:
            logger.exception("Compression summarize failed, skipping")
            return None

        handoff = f"{summary}\n\n工具使用记录：\n{tool_stats}"
        import yuullm

        return [system_msg, yuullm.user(handoff)] + last_step


def _extract_last_step(messages: list) -> list:
    """Extract the last step: last assistant message + all subsequent messages.

    A "step" = one LLM call + its tool results + any pings that arrived.
    Tool calls may be concurrent, so there can be multiple tool results.
    """
    if not messages:
        return []

    # Walk backwards to find the last assistant message
    last_assistant_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], tuple) and len(messages[i]) == 2:
            if messages[i][0] == "assistant":
                last_assistant_idx = i
                break

    if last_assistant_idx is None:
        return messages[-1:] if messages else []

    return messages[last_assistant_idx:]


def _extract_tool_stats(history: list) -> str:
    """Count tool calls in history, pure code extraction."""
    counts: dict[str, int] = {}
    for msg in history:
        if not (isinstance(msg, tuple) and len(msg) == 2):
            continue
        role, items = msg
        if role != "assistant" or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("type") == "tool_call":
                name = item.get("name", "unknown")
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "无工具调用"
    return "\n".join(f"- {name}: {n}次" for name, n in sorted(counts.items()))
