"""Commands sub-application builder and resource type registry factory."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from yuubot.bootstrap.config import ServerConfig
from yuubot.core.tools import ToolRegistry
from yuubot.resources.registry import LifecycleHandler, ResourceTypeRegistry
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.service import ResourceService
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CapabilitySetORM,
    IntegrationORM,
    LLMBackendORM,
    PromptTemplateORM,
)
from yuubot.runtime.daemon.commands._handlers import ResourceCommandHandlers
from yuubot.runtime.daemon.commands._middleware import SecretMiddleware


def build_default_resource_type_registry(
    *,
    integration_lifecycle_handler: LifecycleHandler | None = None,
    actor_lifecycle_handler: LifecycleHandler | None = None,
) -> ResourceTypeRegistry:
    """Create a ResourceTypeRegistry with all known resource types."""
    registry = ResourceTypeRegistry()
    registry.register("llm-backends", LLMBackendORM)
    registry.register(
        "integrations",
        IntegrationORM,
        lifecycle_realm="integrations",
        has_lifecycle=True,
        lifecycle_handler=integration_lifecycle_handler,
    )
    registry.register("capability-sets", CapabilitySetORM)
    registry.register(
        "actors",
        ActorORM,
        lifecycle_realm="actors",
        has_lifecycle=True,
        lifecycle_handler=actor_lifecycle_handler,
    )
    registry.register("ingress-rules", ActorIngressRuleORM)
    registry.register("prompt-templates", PromptTemplateORM)
    return registry


def build_commands_app(
    service: ResourceService,
    type_registry: ResourceTypeRegistry,
    repository: ResourceRepository,
    config: ServerConfig,
    *,
    tool_registry: ToolRegistry | None = None,
) -> Starlette:
    handlers = ResourceCommandHandlers(
        service, type_registry, repository, tool_registry=tool_registry,
    )

    routes = [
        Route("/{resource_type}", handlers.create, methods=["POST"]),
        Route("/{resource_type}", handlers.list_all, methods=["GET"]),
        Route("/{resource_type}/{id}", handlers.get, methods=["GET"]),
        Route("/{resource_type}/{id}", handlers.update, methods=["PUT"]),
        Route("/{resource_type}/{id}", handlers.delete, methods=["DELETE"]),
        Route("/{resource_type}/{id}/{action}", handlers.lifecycle_action, methods=["POST"]),
    ]

    return Starlette(
        routes=routes,
        middleware=[Middleware(SecretMiddleware, secret=config.daemon_secret)],
    )
