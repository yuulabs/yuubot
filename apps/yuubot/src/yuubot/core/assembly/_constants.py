"""Shared constants and helpers for assembly package."""

from __future__ import annotations

PYTHON_PROVIDER_KEY = "execute_python"
RESTART_KERNEL_TOOL_KEY = "restart_kernel"
ROLLOVER_THRESHOLD = 0.85
ROLLOVER_SUMMARY_PROMPT = (
    "Summarize the prior conversation context for continuing the same task. "
    "Preserve user goals, important facts, decisions, open work, tool results, "
    "and any constraints. Return only the summary."
)
IM_MODE_SYSTEM_GUIDANCE = (
    "Yuubot IM mode: incoming mailbox messages are inputs, not function calls. "
    "For user-visible replies, use tim.Channel(path).send(text) to send messages "
    "directly to an integration channel. "
    "Plain assistant text is internal and is not delivered to the IM user."
)
