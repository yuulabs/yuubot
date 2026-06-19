from __future__ import annotations

from attrs import define, field

from yuuagents.types.values import EventPayload


@define
class TaskError:
    type: str
    message: str
    traceback: tuple[str, ...] = ()
    extra: EventPayload = field(factory=dict)
