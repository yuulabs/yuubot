"""Runtime helpers for bridging yuubot capability context into yuuagents."""

from __future__ import annotations

from yuubot.capabilities import CapabilityContext

_CAPABILITY_CONTEXTS: dict[str, CapabilityContext] = {}


def register_capability_context(agent_id: str, context: CapabilityContext) -> None:
    _CAPABILITY_CONTEXTS[agent_id] = context


def unregister_capability_context(agent_id: str) -> None:
    _CAPABILITY_CONTEXTS.pop(agent_id, None)


def capability_context_for_agent(agent_id: str) -> CapabilityContext | None:
    return _CAPABILITY_CONTEXTS.get(agent_id)
