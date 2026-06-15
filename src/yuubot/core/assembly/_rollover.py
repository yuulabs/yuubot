"""History rollover and compaction helpers — pure functions.

These functions compute whether rollover is needed and how to compact
history without side effects. Budgets are owned by the yuubot runtime, not
by yuuagents.Agent.
"""

from __future__ import annotations

import yuullm
from yuuagents import Budget
from yuuagents.agent import Agent

from ._constants import ROLLOVER_SUMMARY_PROMPT, ROLLOVER_THRESHOLD


def _agent_needs_rollover(agent: Agent, budget: Budget | None) -> bool:
    if budget is None:
        return False
    token_limit = budget.limits.get("tokens", 0.0)
    if token_limit <= 0:
        return False
    return budget.usage.get("tokens", 0.0) >= token_limit * ROLLOVER_THRESHOLD


def _summary_history(history: yuullm.History, summarize_steps_span: int) -> yuullm.History:
    messages, _tool_specs = yuullm.split_history(history)
    system_messages = [message for message in messages if message.role == "system"]
    tail_messages = [
        message for message in messages if message.role != "system"
    ][-_positive_span(summarize_steps_span):]
    return [
        *system_messages,
        *tail_messages,
        yuullm.user(ROLLOVER_SUMMARY_PROMPT),
    ]


def _compacted_history(history: yuullm.History, summary: str) -> yuullm.History:
    messages, tool_specs = yuullm.split_history(history)
    result: yuullm.History = []
    if tool_specs is not None:
        result.append(yuullm.tools(tool_specs))
    result.extend(message for message in messages if message.role == "system")
    result.append(
        yuullm.user(
            "The previous context was compacted. Continue from this summary:\n\n"
            f"{summary}"
        )
    )
    return result


def _positive_span(value: int) -> int:
    return value if value > 0 else 20


def _reset_token_usage(agent: Agent, budget: Budget | None) -> Budget | None:
    """Reset token usage by replacing the budget with a fresh instance.

    Budget.usage returns a copy, so mutation through the public API is not
    possible. Instead, create a new Budget with the same limits; the new
    instance starts with empty usage.
    """
    if budget is None:
        return None
    return Budget(limits=budget.limits)


def _last_assistant_text(agent: Agent) -> str:
    messages, _tool_specs = yuullm.split_history(agent.history)
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        text = yuullm.render_message_text(message).strip()
        if text:
            return text
    return ""
