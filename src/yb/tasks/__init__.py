"""Runtime task facade for long-running shell work inside execute_python.

Use ``await submit(name, shell, intro)`` to register fire-and-forget shell tasks
with the daemon Runtime. The call returns a ``Task`` handle immediately after
registration; task execution continues under Runtime even after the current
``execute_python`` tool call ends.

When a task reaches a terminal state, yuubot appends a developer message to the
owner conversation and automatically continues the turn loop. Do not poll HTTP
endpoints for completion; wait for that developer delivery unless you need an
intermediate status check.

Query and control tasks only through this facade. Do not call daemon HTTP routes
such as ``/api/tasks``, ``/api/inbound``, or admin/public APIs directly.

Examples::

    task = await submit("fetch-report", "make build", "Build project artifacts")
    print(task.id, task.status)
    tasks = await list_tasks(name_glob="fetch-*")
    same = await find(task.id)
    print(await same.output())
    await same.cancel()
"""

from __future__ import annotations

import os
from typing import Literal

import httpx

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]


class Task:
    id: str
    name: str
    status: TaskStatus
    intro: str

    def __init__(
        self,
        *,
        id: str,
        name: str,
        status: TaskStatus,
        intro: str,
        _base_url: str,
    ) -> None:
        self.id = id
        self.name = name
        self.status = status
        self.intro = intro
        self._base_url = _base_url.rstrip("/")
        self._payload: dict[str, object] | None = None

    async def _status_payload(self) -> dict[str, object]:
        if self._payload is None:
            self._payload = await _request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        return self._payload

    async def output(self, *, max_bytes: int = 65536) -> str:
        payload = await self._status_payload()
        stdout = payload.get("stdout_tail", "")
        if not isinstance(stdout, str):
            return ""
        return stdout[:max_bytes]

    async def error(self) -> str | None:
        payload = await self._status_payload()
        error = payload.get("error")
        return error if isinstance(error, str) and error else None

    async def exit_code(self) -> int | None:
        payload = await self._status_payload()
        code = payload.get("exit_code")
        return code if isinstance(code, int) else None

    async def cancel(self) -> None:
        self._payload = await _request_json("POST", f"{self._base_url}/api/tasks/{self.id}/cancel")
        status = self._payload.get("status")
        if isinstance(status, str):
            self.status = status  # type: ignore[assignment]


async def submit(name: str, shell: str, intro: str) -> Task:
    base_url = _daemon_url()
    owner = _task_owner()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/api/tasks",
            json={"name": name, "shell": shell, "intro": intro, "owner": owner, "wait_s": 0},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
    return _task_from_payload(payload, base_url=base_url)


async def find(task_id: str) -> Task:
    base_url = _daemon_url()
    payload = await _request_json("GET", f"{base_url}/api/tasks/{task_id}")
    return _task_from_payload(payload, base_url=base_url)


async def list_tasks(*, name_glob: str = "") -> list[Task]:
    base_url = _daemon_url()
    owner = _task_owner()
    params: dict[str, str] = {"owner": owner}
    if name_glob:
        params["name_glob"] = name_glob
    payload = await _request_json("GET", f"{base_url}/api/tasks", params=params)
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [_task_from_payload(item, base_url=base_url) for item in items if isinstance(item, dict)]


def _task_from_payload(payload: dict[str, object], *, base_url: str) -> Task:
    return Task(
        id=str(payload["id"]),
        name=str(payload.get("name", "")),
        status=str(payload.get("status", "pending")),  # type: ignore[arg-type]
        intro=str(payload.get("intro", "")),
        _base_url=base_url,
    )


def _daemon_url() -> str:
    url = os.getenv("YUUBOT_DAEMON_URL")
    if url:
        return url.rstrip("/")
    host = os.getenv("YUUBOT_SERVER_HOST", "127.0.0.1")
    port = os.getenv("YUUBOT_SERVER_PORT", "8765")
    return f"http://{host}:{port}"


def _task_owner() -> str:
    owner = os.getenv("YUUBOT_TASK_OWNER")
    if not owner:
        raise RuntimeError("YUUBOT_TASK_OWNER is required for yb.tasks")
    return owner


async def _request_json(method: str, url: str, params: dict[str, str] | None = None) -> dict[str, object]:
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, params=params, timeout=30.0)
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("unexpected task API response")
    return body
