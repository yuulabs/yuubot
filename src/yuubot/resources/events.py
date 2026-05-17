"""Database-backed resource events."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Literal, Self, cast

from yuubot.core.events import Event

ResourceAction = Literal["inserted", "updated", "deleted"]
RESOURCE_CHANGED_TYPE = "resource.changed"
RESOURCE_ACTIONS: frozenset[str] = frozenset(("inserted", "updated", "deleted"))


@dataclass
class ResourceChanged(Event):
    """A persisted table mutation.

    The event mirrors the database boundary: which table changed, which rows
    changed, and optionally which fields were updated.
    """

    table: str
    action: ResourceAction
    row_ids: tuple[str, ...]
    changed_fields: tuple[str, ...] = ()

    def is_table(self, *tables: str) -> bool:
        return self.table in tables

    def to_dict(self) -> dict[str, object]:
        return {
            "type": RESOURCE_CHANGED_TYPE,
            "table": self.table,
            "action": self.action,
            "row_ids": list(self.row_ids),
            "changed_fields": list(self.changed_fields),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        event_type = payload.get("type")
        if event_type != RESOURCE_CHANGED_TYPE:
            raise ValueError("event type must be 'resource.changed'")

        table = _required_string(payload, "table")
        action = _resource_action(_required_string(payload, "action"))
        row_ids = _string_tuple(payload, "row_ids")
        changed_fields = _string_tuple(payload, "changed_fields", default=())
        return cls(
            table=table,
            action=action,
            row_ids=row_ids,
            changed_fields=changed_fields,
        )


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _resource_action(value: str) -> ResourceAction:
    if value not in RESOURCE_ACTIONS:
        raise ValueError("action must be one of inserted, updated, deleted")
    return cast(ResourceAction, value)


def _string_tuple(
    payload: Mapping[str, object],
    key: str,
    *,
    default: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    value = payload.get(key, default)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(f"{key} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{key} must be a list of non-empty strings")
        result.append(item)
    return tuple(result)
