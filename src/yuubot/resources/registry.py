"""Resource type registry — replaces hardcoded RESOURCE_REGISTRY dict.

Each resource type registers itself with a URL slug and ORM model class.
Handlers (e.g. lifecycle, refresh) are wired separately via event dispatch,
not stored in the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from tortoise import Model

from yuubot.resources.events import ResourceChanged


class ResourceRefreshHandler(Protocol):
    """A callable that handles a ResourceChanged event for a specific table."""

    async def __call__(self, event: ResourceChanged) -> list[str]: ...


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

    _slug_to_orm: dict[str, type[Model]] = field(default_factory=dict)
    _orm_to_slug: dict[type[Model], str] = field(default_factory=dict)

    def register(self, slug: str, orm_type: type[Model]) -> None:
        """Associate a URL slug with its ORM model class."""
        if slug in self._slug_to_orm:
            raise ValueError(f"slug {slug!r} is already registered")
        self._slug_to_orm[slug] = orm_type
        self._orm_to_slug[orm_type] = slug

    def get_orm_type(self, slug: str) -> type[Model] | None:
        """Return the ORM model class for a URL slug, or None."""
        return self._slug_to_orm.get(slug)

    def get_slug(self, orm_type: type[Model]) -> str | None:
        """Return the URL slug for an ORM model class, or None."""
        return self._orm_to_slug.get(orm_type)

    def slugs(self) -> list[str]:
        """Return all registered URL slugs."""
        return sorted(self._slug_to_orm)


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
