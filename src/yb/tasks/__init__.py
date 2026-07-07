"""Runtime task facade for long-running shell work inside execute_python.

Use ``await submit(name, shell, intro)`` to register fire-and-forget shell tasks
with the daemon Runtime. The call returns a ``Task`` handle immediately after
registration; task execution continues under Runtime even after the current
``execute_python`` tool call ends.

Shell tasks run in a PTY with live stdout and stdin support. For interactive
CLI init or login flows, submit the command as a task, inspect output with
``await task.output()`` in a later turn, and send input with ``await task.write(...)``.
Do not use the ``bash`` tool with ``timeout_s`` for interactive or long-running init;
timeouts kill the process.

When a task reaches a terminal state, yuubot appends a developer message to the
owner conversation and automatically continues the turn loop. Do not poll HTTP
endpoints for completion; wait for that developer delivery unless you need an
intermediate status check.

Query and control tasks only through this facade. Do not call daemon HTTP routes
such as ``/api/tasks``, ``/api/inbound``, or admin/public APIs directly.

Durable schedules live under ``yb.tasks.cron``. Use ``await yb.tasks.cron.add(...)``
with an explicit IANA timezone to register recurring cron or one-shot jobs.
Use cron action ``{"kind": "actor_message", "text": "..."}`` for standalone
scheduled actor work, and ``{"kind": "conversation_callback", "text": "..."}``
to continue the owner conversation.

Examples::

    task = await submit("fetch-report", "make build", "Build project artifacts")
    print(task.id, await task.status())
    tasks = await list_tasks(name_glob="fetch-*")
    same = await find(task.id)
    print(await same.output())
    await same.write("yes\\n")
    await same.cancel()
"""

from __future__ import annotations

from typing import Literal

from yb._daemon import daemon_url, request_json, task_owner

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]


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
        status = payload.get("status")
        if isinstance(status, str):
            self._status = status  # type: ignore[assignment]

    async def status(self) -> TaskStatus:
        await self.refresh()
        return self._status

    async def output(self, *, max_bytes: int = 65536) -> str:
        payload = await request_json("GET", f"{self._base_url}/api/tasks/{self.id}")
        status = payload.get("status")
        if isinstance(status, str):
            self._status = status  # type: ignore[assignment]
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
        status = payload.get("status")
        if isinstance(status, str):
            self._status = status  # type: ignore[assignment]


async def submit(name: str, shell: str, intro: str) -> Task:
    base_url = daemon_url()
    payload = await request_json(
        "POST",
        f"{base_url}/api/tasks",
        json={"name": name, "shell": shell, "intro": intro, "owner": task_owner(), "wait_s": 0},
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
    return [_task_from_payload(item, base_url=base_url) for item in items if isinstance(item, dict)]


def _task_from_payload(payload: dict[str, object], *, base_url: str) -> Task:
    return Task(
        id=str(payload["id"]),
        name=str(payload.get("name", "")),
        status=str(payload.get("status", "pending")),  # type: ignore[arg-type]
        intro=str(payload.get("intro", "")),
        _base_url=base_url,
    )


from . import cron as cron  # noqa: E402  # expose yb.tasks.cron after helpers are defined
