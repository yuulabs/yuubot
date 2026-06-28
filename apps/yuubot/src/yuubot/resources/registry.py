"""Resource type registry — replaces hardcoded RESOURCE_REGISTRY dict.

Each resource type registers itself with a URL slug and ORM model class.
Handlers (e.g. lifecycle, refresh) are wired separately via event dispatch,
not stored in the registry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tortoise import Model

from yuubot.resources.events import ResourceChanged


ResourceRefreshHandler = Callable[[ResourceChanged], Awaitable[list[str]]]
LifecycleHandler = Callable[[str, str], Awaitable[list[str]]]


@dataclass
class ResourceTypeDescriptor:
    """Metadata about a resource type, including its lifecycle behavior."""

    orm_type: type[Model]
    lifecycle_handler: LifecycleHandler | None = None


@dataclass
class ResourceTypeRegistry:
    """Maps URL slug strings to ORM model types for resource CRUD routing.

    Usage::

        registry = ResourceTypeRegistry()
        registry.register("integrations", IntegrationORM)
        registry.register("actors", ActorORM)

        orm_type = registry.get_orm_type("integrations")
        slug = registry.get_slug(IntegrationORM)
    """

    _descriptors: dict[str, ResourceTypeDescriptor] = field(default_factory=dict)
    _orm_to_slug: dict[type[Model], str] = field(default_factory=dict)

    def register(
        self,
        slug: str,
        orm_type: type[Model],
        *,
        lifecycle_handler: LifecycleHandler | None = None,
    ) -> None:
        """Associate a URL slug with its ORM model class and lifecycle metadata."""
        if slug in self._descriptors:
            raise ValueError(f"slug {slug!r} is already registered")
        descriptor = ResourceTypeDescriptor(
            orm_type=orm_type,
            lifecycle_handler=lifecycle_handler,
        )
        self._descriptors[slug] = descriptor
        self._orm_to_slug[orm_type] = slug

    def get_orm_type(self, slug: str) -> type[Model] | None:
        """Return the ORM model class for a URL slug, or None."""
        descriptor = self._descriptors.get(slug)
        return descriptor.orm_type if descriptor else None

    def get_slug(self, orm_type: type[Model]) -> str | None:
        """Return the URL slug for an ORM model class, or None."""
        return self._orm_to_slug.get(orm_type)

    def get_descriptor(
        self, slug_or_type: str | type[Model]
    ) -> ResourceTypeDescriptor | None:
        """Return the descriptor for a slug or ORM type, or None."""
        if isinstance(slug_or_type, str):
            return self._descriptors.get(slug_or_type)
        slug = self._orm_to_slug.get(slug_or_type)
        if slug is None:
            return None
        return self._descriptors.get(slug)

    def slugs(self) -> list[str]:
        """Return all registered URL slugs."""
        return sorted(self._descriptors)


@dataclass
class EventDrivenRefreshDispatcher:
    """Dispatches ResourceChanged events to registered per-table handlers.

    Replaces the hardcoded if/elif chain in DaemonRefreshDispatcher with
    a pluggable handler registry::

        dispatcher = EventDrivenRefreshDispatcher()
        dispatcher.on("actors", handle_actor_change)
        dispatcher.on("integrations", handle_integration_change)

        actions = await dispatcher.refresh(event)
    """

    _handlers: dict[str, ResourceRefreshHandler] = field(default_factory=dict)

    def on(self, table: str, handler: ResourceRefreshHandler) -> None:
        """Register a handler for a specific DB table name."""
        self._handlers[table] = handler

    async def refresh(self, event: ResourceChanged) -> list[str]:
        """Dispatch event to the handler registered for its table."""
        handler = self._handlers.get(event.table)
        if handler is None:
            return []
        return await handler(event)
