"""Runtime task facade for long-running shell work inside execute_python.

Register background shell tasks with ``await submit(name, shell, intro, delivery=...)``.
The call returns a ``Task`` handle immediately; execution continues under Runtime
after the current ``execute_python`` tool call ends.

Delivery modes:
- ``manual``: poll with ``task.output()`` / ``task.status()`` yourself; no wakeup
  is sent. Requires ``ttl_s`` greater than 0 and no greater than 3600 seconds.
- ``conversation``: completion appends a developer message and continues the
  owner conversation.
- ``actor``: completion goes to the actor mailbox without binding to a conversation.

Task output is an expiring offload buffer, not durable storage. For long jobs,
write resumable workspace scripts that persist their own state and artifacts.
``task.output(max_bytes=...)`` returns at most 1 MiB from the retained stdout
tail; ``max_bytes`` may request less, but cannot recover output beyond the
retained tail.

For commands that may prompt or need interactive stdin, use the ``bash`` tool.

Durable schedules live under ``yb.tasks.cron`` (see Integration SDKs).

Examples::

    task = await submit("fetch-report", "make build", "Build project artifacts", delivery="manual", ttl_s=3600)
    print(task.id, await task.status())
    tasks = await list_tasks(name_glob="fetch-*")
    same = await find(task.id)
    print(await same.output())
    await same.write("yes\\n")
    await same.cancel()
"""

from __future__ import annotations

from typing import Literal, cast

import msgspec

from yb._daemon import daemon_url, request_json, task_owner

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
TaskDelivery = Literal["manual", "conversation", "actor"]
_TASK_STATUSES: set[str] = {"pending", "running", "done", "failed", "cancelled"}


class _TaskWire(msgspec.Struct, frozen=True):
    id: str
    name: str = ""
    status: str = "pending"
    intro: str = ""
    stdout_tail: str = ""
    error: str | None = None
    exit_code: int | None = None


class _TaskListResponse(msgspec.Struct, frozen=True):
    items: list[_TaskWire] = msgspec.field(default_factory=list)


class Task:
    id: str
    name: str
    intro: str

    def __init__(
        self,
        id: str,
        name: str,
        status: TaskStatus,
        intro: str,
        _base_url: str,
    ) -> None:
        self.id = id
        self.name = name
        self.intro = intro
        self._base_url = _base_url.rstrip("/")
        self._status = status

    async def refresh(self) -> None:
        payload = await request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        self._status = _task_status(msgspec.convert(payload, _TaskWire).status)

    async def status(self) -> TaskStatus:
        await self.refresh()
        return self._status

    async def output(self, max_bytes: int = 1024 * 1024) -> str:
        wire = msgspec.convert(await request_json("GET", f"{self._base_url}/api/tasks/{self.id}"), _TaskWire)
        self._status = _task_status(wire.status)
        return wire.stdout_tail[:max_bytes]

    async def error(self) -> str | None:
        wire = msgspec.convert(await request_json("GET", f"{self._base_url}/api/tasks/{self.id}"), _TaskWire)
        return wire.error if wire.error else None

    async def exit_code(self) -> int | None:
        wire = msgspec.convert(await request_json("GET", f"{self._base_url}/api/tasks/{self.id}"), _TaskWire)
        return wire.exit_code

    async def write(self, text: str) -> None:
        await request_json(
            "POST",
            f"{self._base_url}/api/tasks/{self.id}/stdin",
            json={"text": text},
        )

    async def cancel(self) -> None:
        payload = await request_json("POST", f"{self._base_url}/api/tasks/{self.id}/cancel")
        self._status = _task_status(msgspec.convert(payload, _TaskWire).status)


MAX_MANUAL_TTL_S = 3600.0


async def submit(
    name: str,
    shell: str,
    intro: str,
    delivery: TaskDelivery,
    ttl_s: float | None = None,
) -> Task:
    if ttl_s is not None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be greater than 0")
        if ttl_s > MAX_MANUAL_TTL_S:
            raise ValueError("ttl_s must be <= 3600")
    if delivery == "manual" and ttl_s is None:
        raise ValueError('delivery="manual" requires ttl_s')
    base_url = daemon_url()
    body: dict[str, object] = {
        "name": name,
        "shell": shell,
        "intro": intro,
        "owner": task_owner(),
        "wait_s": 0,
        "delivery": delivery,
    }
    if ttl_s is not None:
        body["ttl_s"] = ttl_s
    payload = await request_json(
        "POST",
        f"{base_url}/api/tasks",
        json=body,
    )
    return _task_from_payload(payload, base_url)


async def find(task_id: str) -> Task:
    base_url = daemon_url()
    payload = await request_json("GET", f"{base_url}/api/tasks/{task_id}")
    return _task_from_payload(payload, base_url)


async def list_tasks(name_glob: str = "") -> list[Task]:
    base_url = daemon_url()
    params: dict[str, str] = {"owner": task_owner()}
    if name_glob:
        params["name_glob"] = name_glob
    payload = await request_json("GET", f"{base_url}/api/tasks", params=params)
    return [_task_from_wire(item, base_url) for item in msgspec.convert(payload, _TaskListResponse).items]


def _task_from_wire(wire: _TaskWire, base_url: str) -> Task:
    return Task(
        wire.id,
        wire.name,
        _task_status(wire.status),
        wire.intro,
        base_url,
    )


def _task_from_payload(payload: dict[str, object], base_url: str) -> Task:
    return _task_from_wire(msgspec.convert(payload, _TaskWire), base_url)


def _task_status(value: str) -> TaskStatus:
    return cast(TaskStatus, value) if value in _TASK_STATUSES else "pending"


from . import cron as cron  # noqa: E402  # expose yb.tasks.cron after helpers are defined
