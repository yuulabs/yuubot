"""Delegation functions exposed to RFC2 Python sessions."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def delegate(agent: str, task: str, *, timeout_s: float | None = None) -> dict[str, Any]:
    """Ask an allowed child agent to work on a task and return its result."""
    return await _p.call("delegate", "delegate", agent=agent, task=task, timeout_s=timeout_s)


async def task_status(name: str | None = None) -> dict[str, Any]:
    """Return status for long-lived Python TASKS entries."""
    tasks = _caller_tasks()
    if name:
        task = tasks.get(name)
        return _task_info(name, task) if task is not None else {"status": "not_found", "name": name}
    return {"tasks": [_task_info(task_name, task) for task_name, task in tasks.items()]}


async def task_cancel(name: str) -> dict[str, Any]:
    """Cancel a long-lived Python TASKS entry."""
    task = _caller_tasks().get(name)
    if task is None:
        return {"status": "not_found", "name": name}
    if isinstance(task, asyncio.Task):
        task.cancel()
        return {"status": "cancelled", "name": name}
    return {"status": "not_cancellable", "name": name}


async def task_result(name: str) -> Any:
    """Return a completed long-lived Python TASKS result."""
    task = _caller_tasks().get(name)
    if task is None:
        return {"status": "not_found", "name": name}
    if isinstance(task, asyncio.Task):
        if not task.done():
            return {"status": "running", "name": name}
        return task.result()
    return task


def _caller_tasks() -> dict[str, Any]:
    frame = inspect.currentframe()
    while frame is not None:
        tasks = frame.f_globals.get("TASKS") or frame.f_locals.get("TASKS")
        if isinstance(tasks, dict):
            return tasks
        frame = frame.f_back
    return {}


def _task_info(name: str, task: Any) -> dict[str, Any]:
    if isinstance(task, asyncio.Task):
        if task.cancelled():
            status = "cancelled"
        elif task.done():
            status = "done"
        else:
            status = "running"
        return {"name": name, "status": status}
    return {"name": name, "status": "value", "type": type(task).__name__}
