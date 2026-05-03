"""Delegation functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Literal, TypedDict

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


__all__ = ["delegate"]


class DelegateResult(TypedDict, total=False):
    id: str
    agent: str
    prompt: str
    status: Literal["running", "finished", "error", "cancelled", "timeout"]
    result: str
    error: str
    created_at: float
    done_at: float | None


async def delegate(agent: str, task: str, *, timeout_s: float | None = None) -> DelegateResult:
    """Run an allowed child agent and return a structured status/result dict.

    If timeout_s expires, returns status="timeout" with error instead of raising
    a service exception.
    """
    return await _p.call("delegate", "delegate", agent=agent, task=task, timeout_s=timeout_s)
