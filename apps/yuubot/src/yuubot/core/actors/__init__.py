"""Actor lifecycle and typed actor runtime contracts."""

from collections.abc import Callable

from yuuagents import ProviderPoolSessionFactory

from yuubot.core.actors.contracts import Actor, ActorFactory
from yuubot.core.actors.events import ActorLifecycleCommand, StartActor, StopActor
from yuubot.core.actors.manager import ActorManager
from yuubot.core.actors.impls.python_session import (
    ActorPythonSessionFactory,
    ExecutePythonSession,
)
from yuubot.core.actors.registry import ActorFactoryRegistry
from yuubot.core.actors.impls.simple_loop import SimpleLoopActor, SimpleLoopActorFactory
from yuubot.core.actors.workspace import ActorWorkspaceResolver, safe_actor_path_id
from yuubot.core.bindings import AgentBinding
from yuubot.core.integrations.core import IntegrationCore
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.resources.repository import ResourceRepository

__all__ = [
    "Actor",
    "ActorFactory",
    "ActorFactoryRegistry",
    "ActorPythonSessionFactory",
    "ActorLifecycleCommand",
    "ActorManager",
    "ActorWorkspaceResolver",
    "ExecutePythonSession",
    "SimpleLoopActor",
    "SimpleLoopActorFactory",
    "StartActor",
    "StopActor",
    "default_actor_factories",
    "safe_actor_path_id",
]


def default_actor_factories(
    python_sessions: ActorPythonSessionFactory,
    repository: ResourceRepository,
    trace_context: YuubotTraceContextProvider | None = None,
    integrations: IntegrationCore | None = None,
    llm_session_factory_factory: (
        Callable[[AgentBinding], ProviderPoolSessionFactory | None] | None
    ) = None,
) -> ActorFactoryRegistry:
    registry = ActorFactoryRegistry()
    registry.register(
        SimpleLoopActorFactory(
            repository=repository,
            python_sessions=python_sessions,
            integrations=integrations,
            trace_context=trace_context,
            llm_session_factory_factory=llm_session_factory_factory,
        )
    )
    return registry
