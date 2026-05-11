"""Explicit resource references round-trip through generated ORM models."""

from __future__ import annotations

import msgspec
from tortoise import Tortoise

from yuubot.resources.orm import from_orm, to_orm_fields
from yuubot.resources.store.model_factory import char, reference, resource_model


class ReferencedRecord(msgspec.Struct):
    id: str
    value: str


class ParentRecord(msgspec.Struct):
    id: str
    child: ReferencedRecord


ReferencedORM = resource_model(
    "ReferencedORM",
    ReferencedRecord,
    table="test_referenced_records",
    module=__name__,
    field_specs={"id": char(primary_key=True)},
)

ParentORM = resource_model(
    "ParentORM",
    ParentRecord,
    table="test_parent_records",
    module=__name__,
    field_specs={"id": char(primary_key=True)},
    references={"child": reference(ReferencedORM)},
)


async def test_explicit_reference_round_trips_as_nested_record():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": [__name__]})
    try:
        await Tortoise.generate_schemas()
        child = ReferencedRecord(id="child-1", value="current")
        parent = ParentRecord(id="parent-1", child=child)

        await ReferencedORM.create(**to_orm_fields(child, ReferencedORM))
        row = await ParentORM.create(**to_orm_fields(parent, ParentORM))

        assert row.child_id == child.id

        fetched = await ParentORM.get(id=parent.id).select_related("child")
        restored = await from_orm(fetched, ParentRecord)

        assert restored == parent
    finally:
        await Tortoise.close_connections()
