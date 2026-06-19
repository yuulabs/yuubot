"""Task primitives: Task, TaskStatus, TaskError, Owner, OwnerType."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from enum import StrEnum
from typing import Any, Generic, TypeVar

from attrs import define, field

from yuuagents.obs.entitylog import EntityLog
from yuuagents.types.errors import TaskError

T = TypeVar("T")


class OwnerType(StrEnum):
    """Who created a task — agent, parent task, or system."""

    AGENT = "agent"
    TASK = "task"
    SYSTEM = "system"


@define(frozen=True)
class Owner:
    """Unified ownership model for tasks.

    Replaces separate agent_id/parent_id fields. All tasks have an owner
    that describes who created them.
    """

    type: OwnerType
    id: str


class TaskStatus(StrEnum):
    """Observable lifecycle status for a Task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@define
class Task(Generic[T]):
    """Container for any async unit of work — observable, controllable.

    ═══════════════════════════════════════════════════════════════════
    NOTE: This is a concrete class designed to be *subclassed* for
    specific task types (e.g. ToolCallTask). This is an intentional
    exception to "prefer composition over inheritance": Task is a
    thin lifecycle container close to a protocol, and the inheritance
    cost is negligible.
    ═══════════════════════════════════════════════════════════════════

    The type parameter T is the result type (coro returns T → result: T).
    """

    id: str
    owner: Owner
    status: TaskStatus = TaskStatus.PENDING

    stdin: asyncio.StreamWriter | None = None
    stdout: EntityLog = field(factory=EntityLog)
    stderr: EntityLog = field(factory=EntityLog)

    # Escape hatch for user-defined metadata. Subclasses can also
    # extend the ``info`` property below to include computed fields.
    info_data: dict[str, Any] = field(factory=dict, repr=False)
    # Set by Runtime.cancel_task(), read in _run_task().
    cancel_reason: str | None = field(default=None, init=False, repr=False)

    result: T | None = None
    error: TaskError | None = None

    coro: Coroutine[Any, Any, T] | None = None

    @property
    def info(self) -> dict[str, Any]:
        """Observable metadata published with lifecycle events.

        Subclasses should override this to add type-specific fields
        (e.g. tool_name, tool_call_id for ToolCallTask).
        """
        d = dict(self.info_data)
        d["task_id"] = self.id
        d["owner_type"] = self.owner.type.value
        d["owner_id"] = self.owner.id
        d["status"] = self.status.value
        if self.error is not None:
            d["error"] = str(self.error)
        if self.cancel_reason is not None:
            d["cancel_reason"] = self.cancel_reason
        return d
