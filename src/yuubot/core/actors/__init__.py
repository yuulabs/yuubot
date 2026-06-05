"""Actor lifecycle and typed actor runtime contracts."""

from yuubot.bootstrap.config import YuuAgentsConfig
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
    config: YuuAgentsConfig,
    python_sessions: ActorPythonSessionFactory,
    repository: ResourceRepository,
    trace_context: YuubotTraceContextProvider | None = None,
    integrations: IntegrationCore | None = None,
) -> ActorFactoryRegistry:
    registry = ActorFactoryRegistry()
    registry.register(
        SimpleLoopActorFactory(
            repository=repository,
            yuuagents_config=config,
            python_sessions=python_sessions,
            integrations=integrations,
            trace_context=trace_context,
        )
    )
    return registry
