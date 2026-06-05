"""Background task helpers for the handwritten yb facade."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import suppress
from typing import Any

from yb import _client, _context


def submit_bg(coro: Any, tid_suggest: str | None = None) -> str:
    """Submit a long-running coroutine as an actor background task."""
    task_id = _task_id(tid_suggest)
    task = asyncio.create_task(coro)
    _tasks()[task_id] = task
    asyncio.create_task(_notify_background_started(task_id))
    task.add_done_callback(
        lambda done: asyncio.create_task(_finish_background(task_id, done))
    )
    return task_id


async def _notify_background_started(task_id: str) -> None:
    with suppress(Exception):
        await _client.request(
            _background_request("background_started", task_id, status="running")
        )


async def _finish_background(task_id: str, task: asyncio.Task[Any]) -> None:
    status, summary = _summarize_task(task)
    with suppress(Exception):
        await _client.request(
            _background_request(
                "background_finished",
                task_id,
                status=status,
                summary=summary,
            )
        )


def _background_request(
    kind: str,
    task_id: str,
    *,
    status: str,
    summary: str = "",
) -> dict[str, Any]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return {
        "token": bridge.token,
        "kind": kind,
        "actor_id": actor.actor_id,
        "agent_name": actor.agent_name,
        "session_id": actor.session_id,
        "mailbox_id": actor.mailbox_id,
        "task_id": task_id,
        "status": status,
        "summary": summary,
    }


def _tasks() -> dict[str, asyncio.Task[Any]]:
    main = sys.modules.get("__main__")
    tasks = getattr(main, "TASKS", None)
    if isinstance(tasks, dict):
        return tasks
    local_tasks = globals().setdefault("TASKS", {})
    return local_tasks


def _task_id(tid_suggest: str | None) -> str:
    tasks = _tasks()
    base = str(tid_suggest).strip() if tid_suggest else ""
    if not base:
        base = uuid.uuid4().hex
    task_id = base
    while task_id in tasks:
        task_id = f"{base}-{uuid.uuid4().hex[:8]}"
    return task_id


def _summarize_task(task: asyncio.Task[Any]) -> tuple[str, str]:
    if task.cancelled():
        return "cancelled", "cancelled"
    try:
        result = task.result()
    except BaseException as exc:
        return "error", _bounded(f"{type(exc).__name__}: {exc}")
    if result is None:
        return "ok", "completed with no result"
    if isinstance(result, str):
        return "ok", _bounded(result)
    with suppress(Exception):
        return "ok", _bounded(json.dumps(result, ensure_ascii=False, default=repr))
    return "ok", _bounded(repr(result))


def _bounded(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... truncated ..."
