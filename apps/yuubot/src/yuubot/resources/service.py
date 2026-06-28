"""ResourceService — domain layer for resource CRUD and lifecycle operations.

Separates business logic (DB ops, reconcile, lifecycle) from HTTP request
handling. HTTP handlers parse/validate input and format output; this service
owns the actual domain operations.
"""

from __future__ import annotations


from dataclasses import dataclass

import msgspec
from tortoise import Model
from tortoise.exceptions import BaseORMException

from yuubot.core.actors.manager import ActorManager
from yuubot.core.integrations.core import IntegrationCore
from yuubot.resources.errors import StorageError
from yuubot.resources.events import ResourceAction, ResourceChanged
from yuubot.resources.registry import EventDrivenRefreshDispatcher, ResourceTypeRegistry
from yuubot.resources.repository import ResourceRepository


@dataclass
class ResourceService:
    """Domain service for resource CRUD with reconcile dispatch.

    HTTP handlers delegate to this service for all data mutations and
    lifecycle operations. The service handles DB operations and triggers
    runtime reconciliation, returning (result, actions, warnings) tuples
    that the HTTP layer formats into responses.
    """

    repository: ResourceRepository
    refresh: EventDrivenRefreshDispatcher
    integrations: IntegrationCore
    actors: ActorManager
    type_registry: ResourceTypeRegistry

    async def create(
        self,
        orm_type: type[Model],
        record: msgspec.Struct,
    ) -> tuple[msgspec.Struct, list[str], list[str]]:
        """Insert a resource record and trigger reconcile after commit."""
        row_id: str = getattr(record, "id")
        try:
            inserted = await self.repository.insert(orm_type, record)
        except BaseORMException as exc:
            raise StorageError(str(exc)) from exc
        table = orm_type._meta.db_table
        actions, warnings = await self._reconcile(table, "inserted", row_id)
        return inserted, actions, warnings

    async def update(
        self,
        orm_type: type[Model],
        row_id: str,
        **fields: object,
    ) -> tuple[msgspec.Struct | None, list[str], list[str]]:
        """Update a resource record and trigger reconcile after commit."""
        try:
            updated = await self.repository.update(orm_type, row_id, **fields)
        except BaseORMException as exc:
            raise StorageError(str(exc)) from exc
        if updated is None:
            return None, [], []
        table = orm_type._meta.db_table
        actions, warnings = await self._reconcile(
            table, "updated", row_id, tuple(fields.keys())
        )
        return updated, actions, warnings

    async def delete(
        self,
        orm_type: type[Model],
        row_id: str,
    ) -> tuple[bool, list[str], list[str]]:
        """Delete a resource record and trigger reconcile after commit."""
        try:
            deleted = await self.repository.delete(orm_type, row_id)
        except BaseORMException as exc:
            raise StorageError(str(exc)) from exc
        if not deleted:
            return False, [], []
        table = orm_type._meta.db_table
        actions, warnings = await self._reconcile(table, "deleted", row_id)
        return True, actions, warnings

    async def set_enabled(
        self,
        orm_type: type[Model],
        row_id: str,
        enabled: bool,
    ) -> tuple[msgspec.Struct | None, list[str], list[str]]:
        """Enable or disable a resource and trigger lifecycle reconcile."""
        label = "enable" if enabled else "disable"
        try:
            updated = await self.repository.update(orm_type, row_id, enabled=enabled)
        except BaseORMException as exc:
            raise StorageError(str(exc)) from exc
        if updated is None:
            return None, [], []

        descriptor = self.type_registry.get_descriptor(orm_type)
        handler = descriptor.lifecycle_handler if descriptor is not None else None
        if handler is None:
            return updated, [], []

        actions = await handler(row_id, label)
        return updated, actions, []

    async def _reconcile(
        self,
        table: str,
        action: ResourceAction,
        row_id: str,
        changed_fields: tuple[str, ...] = (),
    ) -> tuple[list[str], list[str]]:
        """Dispatch a ResourceChanged event through the refresh dispatcher."""
        event = ResourceChanged(
            table=table,
            action=action,
            row_ids=(row_id,),
            changed_fields=changed_fields,
        )
        actions = await self.refresh.refresh(event)
        return actions, []
