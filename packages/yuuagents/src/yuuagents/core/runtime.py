"""New Runtime — observable task manager for tool execution.

Replaces the old ToolExecutor-based Runtime with a Task-based model.
Each tool call becomes a Task with stdout/stderr EntityLogs, status tracking,
and explicit lifecycle (submit -> wait/cancel/detach).

Supports submitting arbitrary coroutines via submit_task() so that
external code (e.g. agent loops) can be managed as Runtime tasks.
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import yuullm
from attrs import define, field

from yuuagents.obs.entitylog import EntityLog
from yuuagents.core.eventbus import EventBus
from yuuagents.core.task import Owner, OwnerType, Task, TaskError, TaskStatus
from yuuagents.tool.primitives import (
    ToolCallParams,
    ToolCallTask,
    ToolContext,
    ToolRegistry,
)

T = TypeVar("T")


def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


_TASK_STATUS_EVENTS: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "runtime.task_pending",
    TaskStatus.RUNNING: "runtime.task_running",
    TaskStatus.COMPLETED: "runtime.task_completed",
    TaskStatus.FAILED: "runtime.task_failed",
    TaskStatus.CANCELLED: "runtime.task_cancelled",
    TaskStatus.TIMED_OUT: "runtime.task_timed_out",
}


@define
class Runtime:
    """Manages tool Task lifecycle: submit, wait, cancel, detach, kill.

    Holds a ToolRegistry, EventBus, and indexes for task lookup.
    """

    registry: ToolRegistry
    eventbus: EventBus
    _tasks: dict[str, Task] = field(factory=dict, init=False, repr=False)
    _run_tasks: dict[str, asyncio.Task[None]] = field(
        factory=dict, init=False, repr=False
    )
    _owner_index: dict[tuple[str, str], set[str]] = field(
        factory=dict, init=False, repr=False
    )

    async def submit_tool_call(
        self,
        owner: Owner,
        tool_call: yuullm.ToolCall,
        context: ToolContext,
    ) -> Task:
        """Submit a tool call for execution. Returns immediately with a Task."""
        definition, tool = self.registry.resolve(tool_call.name)
        params = definition.input_model.model_validate(tool_call.arguments_dict())

        task = ToolCallTask(
            id=_generate_task_id(),
            owner=owner,
            tool_call_params=ToolCallParams(
                tool_call_id=tool_call.id,
                tool_name=definition.name,
                params=params,
            ),
            status=TaskStatus.PENDING,
            stdout=EntityLog(),
            stderr=EntityLog(),
        )

        context.task_id = task.id
        task.coro = tool.create_coro(task, context)
        self._tasks[task.id] = task
        # Maintain owner -> task_ids index
        key = (owner.type.value, owner.id)
        self._owner_index.setdefault(key, set()).add(task.id)
        run_task = asyncio.create_task(self._run_task(task))
        self._run_tasks[task.id] = run_task
        return task

    async def submit_task(
        self,
        owner: Owner,
        factory: Callable[[Task[T]], Coroutine[Any, Any, T]],
        *,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task[T]:
        """Submit an arbitrary coroutine as a managed Runtime task.

        Unlike submit_tool_call(), this accepts a coroutine **factory**
        that receives the Task instance so that the caller can write to
        ``task.stdout`` / ``task.stderr`` for observability.

        The factory is invoked synchronously after Task creation and its
        returned coroutine is executed by the Runtime lifecycle.

        Parameters
        ----------
        owner:
            Who owns this task (typically ``Owner(type=OwnerType.AGENT, id=agent_id)``
            or ``Owner(type=OwnerType.SYSTEM, id=...)``).
        factory:
            ``async def factory(task: Task[T]) -> T`` — receives the created Task
            so the caller can write to ``task.stdout`` / ``task.stderr``, inspect
            ``task.info_data``, etc.
        task_id:
            Optional explicit task id. Auto-generated if not provided.
        metadata:
            Optional user-defined dict merged into ``task.info``.
        """
        tid = task_id or _generate_task_id()
        task: Task[T] = Task(
            id=tid,
            owner=owner,
            status=TaskStatus.PENDING,
            info_data=dict(metadata) if metadata else {},
        )
        task.coro = factory(task)
        self._tasks[tid] = task
        key = (owner.type.value, owner.id)
        self._owner_index.setdefault(key, set()).add(tid)
        run_task = asyncio.create_task(self._run_task(task))
        self._run_tasks[tid] = run_task
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """Look up a task by id. Returns None if not found."""
        return self._tasks.get(task_id)

    async def list_tasks(self, owner: Owner | None = None) -> list[Task]:
        """List all tasks, optionally filtered by owner."""
        if owner is None:
            return list(self._tasks.values())
        key = (owner.type.value, owner.id)
        task_ids = self._owner_index.get(key, set())
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    async def wait_task(self, task_id: str, timeout: float | None = None) -> Task:
        """Wait for a task to complete. Returns the task with result/error set."""
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
        ):
            return task
        run_task = self._run_tasks.get(task_id)
        if run_task is not None and not run_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(run_task),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                task.status = TaskStatus.TIMED_OUT
                task.error = TaskError(
                    type="timeout",
                    message=f"Task timed out after {timeout}s",
                )
                await self._emit_task_event(task)
            except asyncio.CancelledError, Exception:
                pass
        return task

    async def cancel_task(self, task_id: str, reason: str) -> Task:
        """Cancel a running or pending task. Has no effect on completed/failed tasks."""
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return task
        task.cancel_reason = reason
        run_task = self._run_tasks.get(task_id)
        if run_task is not None and not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError, Exception:
                pass
        # Fallback: if _run_task didn't finalize (e.g. coroutine never started)
        if task.status not in (
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.FAILED,
        ):
            task.status = TaskStatus.CANCELLED
            task.error = TaskError(type="cancelled", message=reason)
            await self._emit_task_event(task)
        return task

    async def kill_task(self, task_id: str, reason: str) -> Task:
        """Force-kill a task immediately (same as cancel in this implementation)."""
        return await self.cancel_task(task_id, reason)

    async def detach_agent_tasks(self, agent_id: str) -> list[Task]:
        """Let all running tasks for an agent continue in background.

        Returns the list of tasks that were detached (owner index is preserved
        so tasks remain findable).
        """
        key = (OwnerType.AGENT.value, agent_id)
        task_ids = list(self._owner_index.get(key, set()))
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    async def close(self) -> None:
        """Cancel all running tasks and release resources."""
        task_ids = list(self._tasks.keys())
        for tid in task_ids:
            run_task = self._run_tasks.get(tid)
            if run_task is not None and not run_task.done():
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError, Exception:
                    pass
        self._tasks.clear()
        self._run_tasks.clear()
        self._owner_index.clear()

    async def cancel_agent_tasks(
        self, agent_id: str, recursive: bool = True
    ) -> list[Task]:
        """Cancel all tasks for an agent (and optionally subtasks)."""
        key = (OwnerType.AGENT.value, agent_id)
        task_ids = list(self._owner_index.get(key, set()))
        cancelled: list[Task] = []
        for tid in task_ids:
            if tid in self._tasks:
                cancelled.append(await self.cancel_task(tid, "agent cancelled"))
        return cancelled

    # ── Internal ─────────────────────────────────────────────

    async def _run_task(self, task: Task) -> None:
        """Execute the tool coroutine with lifecycle management."""
        task.status = TaskStatus.RUNNING
        try:
            if task.coro is None:
                raise RuntimeError(f"Task {task.id} has no coroutine")
            result = await task.coro
            if task.error is None and task.status not in (
                TaskStatus.CANCELLED,
                TaskStatus.TIMED_OUT,
            ):
                task.status = TaskStatus.COMPLETED
                task.result = result
        except asyncio.CancelledError:
            cancel_reason = task.cancel_reason or "task cancelled"
            task.status = TaskStatus.CANCELLED
            task.error = TaskError(type="cancelled", message=cancel_reason)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = TaskError(
                type="runtime",
                message=str(e),
                traceback=tuple(traceback.format_exc().splitlines()),
            )
        await self._emit_task_event(task)

    async def _emit_task_event(self, task: Task) -> None:
        """Emit task lifecycle event to eventbus."""
        event_name = _TASK_STATUS_EVENTS[task.status]
        await self.eventbus.emit(
            event_name,
            task.info,
        )
