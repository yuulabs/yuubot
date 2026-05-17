"""Generated async RPC client template for yext facade functions.

This module holds the source code that gets written into the generated yext
package as ``_client.py``. It lives here as real Python so it can be linted,
type-checked, and tested independently of the code-generation machinery.
"""

from __future__ import annotations

YEXT_CONTEXT_MODULE = "yuubot_yext_context"

_CLIENT_SOURCE = '''\
"""Generated async RPC client for yext facade functions."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import suppress
from collections.abc import Mapping
from typing import Any

import {context_module} as _context


async def invoke(capability_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = {{
        "token": _context.TOKEN,
        "kind": "invoke",
        "actor_id": _context.ACTOR_ID,
        "capability_id": capability_id,
        "payload": payload,
    }}
    response = await _request(request)
    result = response.get("result", {{}})
    if not isinstance(result, dict):
        raise TypeError("integration facade result must be a JSON object")
    return result


def submit_bg(coro: Any, tid_suggest: str | None = None) -> str:
    """Submit a long-running coroutine as a background task.

    Use this when work may take a long time. The daemon will notify you when
    the task completes. Keep the returned task id, and after the notification
    inspect the completed asyncio.Task with TASKS[task_id], for example:
    task = TASKS[task_id]; result = task.result().
    """
    task_id = _task_id(tid_suggest)
    task = asyncio.create_task(coro)
    _tasks()[task_id] = task
    asyncio.create_task(_notify_background_started(task_id))
    task.add_done_callback(lambda done: asyncio.create_task(_finish_background(task_id, done)))
    return task_id


async def _notify_background_started(task_id: str) -> None:
    with suppress(Exception):
        await _request(_background_request("background_started", task_id, status="running"))


async def _finish_background(task_id: str, task: asyncio.Task[Any]) -> None:
    status, summary = _summarize_task(task)
    with suppress(Exception):
        await _request(_background_request(
            "background_finished",
            task_id,
            status=status,
            summary=summary,
        ))


def _background_request(
    kind: str,
    task_id: str,
    *,
    status: str,
    summary: str = "",
) -> dict[str, Any]:
    return {{
        "token": _context.TOKEN,
        "kind": kind,
        "actor_id": _context.ACTOR_ID,
        "agent_name": _context.AGENT_NAME,
        "session_id": _context.SESSION_ID,
        "mailbox_id": _context.MAILBOX_ID,
        "task_id": task_id,
        "status": status,
        "summary": summary,
    }}


async def _request(request: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(_context.HOST, _context.PORT),
        timeout=_context.TIMEOUT_S,
    )
    try:
        writer.write(json.dumps(request, ensure_ascii=True).encode() + b"\\n")
        await writer.drain()
        raw_response = await asyncio.wait_for(
            reader.readline(),
            timeout=_context.TIMEOUT_S,
        )
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
    if not raw_response:
        raise RuntimeError("integration facade call returned no response")
    response = json.loads(raw_response.decode())
    if not response.get("ok"):
        error = response.get("error", {{}})
        error_type = error.get("type", "RuntimeError")
        message = error.get("message", "integration facade call failed")
        raise RuntimeError(f"{{error_type}}: {{message}}")
    return response


def coerce_payload(value: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return dict(payload)
    if not payload and isinstance(value, Mapping):
        return dict(value)
    return {{"value": value, **payload}}


def _tasks() -> dict[str, asyncio.Task[Any]]:
    main = sys.modules.get("__main__")
    tasks = getattr(main, "TASKS", None)
    if isinstance(tasks, dict):
        return tasks
    local_tasks = globals().setdefault("TASKS", {{}})
    return local_tasks


def _task_id(tid_suggest: str | None) -> str:
    tasks = _tasks()
    base = str(tid_suggest).strip() if tid_suggest else ""
    if not base:
        base = uuid.uuid4().hex
    task_id = base
    while task_id in tasks:
        task_id = f"{{base}}-{{uuid.uuid4().hex[:8]}}"
    return task_id


def _summarize_task(task: asyncio.Task[Any]) -> tuple[str, str]:
    if task.cancelled():
        return "cancelled", "cancelled"
    try:
        result = task.result()
    except BaseException as exc:
        return "error", _bounded(f"{{type(exc).__name__}}: {{exc}}")
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
    return text[:limit] + "\\n... truncated ..."
'''


def render_client_module(context_module: str = YEXT_CONTEXT_MODULE) -> str:
    """Return the generated _client.py source with the context module name substituted."""
    return _CLIENT_SOURCE.format(context_module=context_module)
