"""Direct schema repository with table-level change events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

import msgspec
from tortoise import Model

from yuubot.core.events import EventBus
from yuubot.core.secrets import SecretCodec
from yuubot.resources.events import ResourceAction, ResourceChanged
from yuubot.resources.orm import (
    from_orm,
    referenced_field_names,
    to_orm_fields,
    to_orm_update_fields,
)
from yuubot.resources.store.protocol import schema_type_of
from yuubot.resources.store.resource import Store

RecordT = TypeVar("RecordT", bound=msgspec.Struct)
OrmT = TypeVar("OrmT", bound=Model)


class HasId(Protocol):
    id: str


@dataclass
class ResourceRepository:
    """Thin CRUD boundary over Tortoise models and msgspec records."""

    store: Store
    event_bus: EventBus
    secret_codec: SecretCodec | None = None

    async def insert(self, row_type: type[OrmT], record: RecordT) -> RecordT:
        async with self.store.transaction():
            with self.store.db.activate():
                await row_type.create(
                    **to_orm_fields(
                        record,
                        row_type,
                        secret_codec=self.secret_codec,
                    )
                )
                query = row_type.get(id=self._row_id(record))
                query = query.select_related(*referenced_field_names(row_type))
                row = await query
        inserted = await self._record_from_row(row)
        self._publish(row_type, "inserted", self._row_id(inserted))
        return cast(RecordT, inserted)

    async def get(self, row_type: type[OrmT], row_id: str) -> RecordT | None:
        with self.store.db.activate():
            query = row_type.get_or_none(id=row_id)
            query = query.select_related(*referenced_field_names(row_type))
            row = await query
        if row is None:
            return None
        return cast(RecordT, await self._record_from_row(row))

    async def list(self, row_type: type[OrmT]) -> tuple[RecordT, ...]:
        with self.store.db.activate():
            query = row_type.all().order_by("id")
            query = query.select_related(*referenced_field_names(row_type))
            rows = await query
        records: list[RecordT] = []
        for row in rows:
            records.append(cast(RecordT, await self._record_from_row(row)))
        return tuple(records)

    async def update(
        self,
        row_type: type[OrmT],
        row_id: str,
        **fields: object,
    ) -> RecordT | None:
        if not fields:
            return await self.get(row_type, row_id)
        async with self.store.transaction():
            with self.store.db.activate():
                orm_fields = to_orm_update_fields(
                    row_type,
                    cast(dict[str, Any], fields),
                    secret_codec=self.secret_codec,
                )
                count = await row_type.filter(id=row_id).update(**orm_fields)
                if count == 0:
                    return None
                query = row_type.get(id=row_id)
                query = query.select_related(*referenced_field_names(row_type))
                row = await query
        updated = await self._record_from_row(row)
        self._publish(row_type, "updated", row_id, tuple(fields))
        return cast(RecordT, updated)

    async def delete(self, row_type: type[OrmT], row_id: str) -> bool:
        async with self.store.transaction():
            with self.store.db.activate():
                count = await row_type.filter(id=row_id).delete()
        if count == 0:
            return False
        self._publish(row_type, "deleted", row_id)
        return True

    async def _record_from_row(self, row: Model) -> msgspec.Struct:
        schema_type = schema_type_of(type(row))
        return await from_orm(row, schema_type, secret_codec=self.secret_codec)

    def _publish(
        self,
        row_type: type[Model],
        action: ResourceAction,
        row_id: str,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        self.event_bus.publish(
            ResourceChanged(
                table=self._table_name(row_type),
                action=action,
                row_ids=(row_id,),
                changed_fields=changed_fields,
            )
        )

    def _row_id(self, record: object) -> str:
        """Extract the string id from a record.

        Accepts ``object`` for compatibility with ``msgspec.Struct`` types
        that declare ``id: str`` at the concrete level but not on the base
        class.  The ``HasId`` Protocol documents the required shape.
        """
        row_id = getattr(record, "id", None)
        if not isinstance(row_id, str):
            raise ValueError(f"{type(record).__name__} must have a string id")
        return row_id

    def _table_name(self, row_type: type[Model]) -> str:
        return row_type._meta.db_table
