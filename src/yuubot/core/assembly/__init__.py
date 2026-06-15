"""yuuagents actor assembly — Stage construction, agent definition building,
and actor runtime orchestration.

Public API::

    from yuubot.core.assembly import (
        YuuAgentsActorRuntime,
        build_agent_definition,
        start_yuuagents_actor,
    )
"""

from __future__ import annotations

from yuubot.core.assembly._definition import build_agent_definition
from yuubot.core.assembly._llm_session import llm_session_factory_for_binding
from yuubot.core.assembly._runtime import YuuAgentsActorRuntime
from yuubot.core.assembly._stage import start_yuuagents_actor

__all__ = [
    "YuuAgentsActorRuntime",
    "build_agent_definition",
    "llm_session_factory_for_binding",
    "start_yuuagents_actor",
]
