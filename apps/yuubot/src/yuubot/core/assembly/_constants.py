"""Shared constants and helpers for assembly package."""

from __future__ import annotations

from yuuagents import PythonImport

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
FACADE_IMPORTS = (
    PythonImport(module="yb"),
    PythonImport(module="yb.actor"),
    PythonImport(module="yb.delegate"),
    PythonImport(module="yb.schedule"),
    PythonImport(module="yb.tasks"),
    PythonImport(module="tim"),
)
FACADE_EXPAND_FUNCTIONS = (
    "yb.*",
    "yb.actor.*",
    "yb.delegate.*",
    "yb.schedule.*",
    "yb.tasks.*",
    "tim.*",
)

_YUUAGENTS_KNOWN_FACTORIES: frozenset[str] = frozenset({"openai", "anthropic", "openrouter"})


def _resolve_yuuagents_provider(yuuagents_provider: str) -> str:
    """Map a provider name to the yuuagents LLM factory name.

    Known yuuagents factory names pass through directly.  Any other value
    (vendor names like ``"deepseek"``, ``"groq"``, etc.) resolves to
    ``"openai"`` since those vendors use the OpenAI-compatible wire protocol.
    """
    if yuuagents_provider in _YUUAGENTS_KNOWN_FACTORIES:
        return yuuagents_provider
    return "openai"
