"""Runtime task facade for long-running shell work inside execute_python.

Register background shell tasks with ``await submit(name, shell, intro, delivery=...)``.
The call returns a ``Task`` handle immediately; execution continues under Runtime
after the current ``execute_python`` tool call ends.

Delivery modes:
- ``manual``: poll with ``task.output()`` / ``task.status()`` yourself; no wakeup
  is sent. Requires ``ttl_s`` no greater than 3600 seconds.
- ``conversation``: completion appends a developer message and continues the
  owner conversation.
- ``actor``: completion goes to the actor mailbox without binding to a conversation.

Task output is an expiring offload buffer, not durable storage. For long jobs,
write resumable workspace scripts that persist their own state and artifacts.

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

from yb._daemon import daemon_url, request_json, task_owner

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
TaskDelivery = Literal["manual", "conversation", "actor"]
_TASK_STATUSES: set[str] = {"pending", "running", "done", "failed", "cancelled"}


class Task:
    id: str
    name: str
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
        self.intro = intro
        self._base_url = _base_url.rstrip("/")
        self._status = status

    async def refresh(self) -> None:
        payload = await request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        self._status = _task_status(payload.get("status"))

    async def status(self) -> TaskStatus:
        await self.refresh()
        return self._status

    async def output(self, *, max_bytes: int = 65536) -> str:
        payload = await request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        self._status = _task_status(payload.get("status"))
        stdout = payload.get("stdout_tail", "")
        if not isinstance(stdout, str):
            return ""
        return stdout[:max_bytes]

    async def error(self) -> str | None:
        payload = await request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        error = payload.get("error")
        return error if isinstance(error, str) and error else None

    async def exit_code(self) -> int | None:
        payload = await request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        code = payload.get("exit_code")
        return code if isinstance(code, int) else None

    async def write(self, text: str) -> None:
        await request_json(
            "POST",
            f"{self._base_url}/api/tasks/{self.id}/stdin",
            json={"text": text},
        )

    async def cancel(self) -> None:
        payload = await request_json("POST", f"{self._base_url}/api/tasks/{self.id}/cancel")
        self._status = _task_status(payload.get("status"))


MAX_MANUAL_TTL_S = 3600.0


async def submit(
    name: str,
    shell: str,
    intro: str,
    *,
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
    return _task_from_payload(payload, base_url=base_url)


async def find(task_id: str) -> Task:
    base_url = daemon_url()
    payload = await request_json("GET", f"{base_url}/api/tasks/{task_id}")
    return _task_from_payload(payload, base_url=base_url)


async def list_tasks(*, name_glob: str = "") -> list[Task]:
    base_url = daemon_url()
    params: dict[str, str] = {"owner": task_owner()}
    if name_glob:
        params["name_glob"] = name_glob
    payload = await request_json("GET", f"{base_url}/api/tasks", params=params)
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [_task_from_payload(cast(dict[str, object], item), base_url=base_url) for item in items if isinstance(item, dict)]


def _task_from_payload(payload: dict[str, object], *, base_url: str) -> Task:
    return Task(
        id=str(payload["id"]),
        name=str(payload.get("name", "")),
        status=_task_status(payload.get("status")),
        intro=str(payload.get("intro", "")),
        _base_url=base_url,
    )


def _task_status(value: object) -> TaskStatus:
    return cast(TaskStatus, value if isinstance(value, str) and value in _TASK_STATUSES else "pending")


from . import cron as cron  # noqa: E402  # expose yb.tasks.cron after helpers are defined
