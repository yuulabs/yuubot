"""Durable cron-job facade for ``execute_python``.

Create a job with ``await add(name, timezone=..., cron=..., action=...)`` or
``await add(name, timezone=..., at=..., once=True, action=...)``. ``timezone``
must be an IANA name such as ``Asia/Shanghai``. Supply exactly one schedule:
``cron`` is a cron expression, while ``at`` is either a timezone-naive local
ISO datetime (``YYYY-MM-DDTHH:MM:SS``) or a relative delay such as ``+1m``.

Actions are dictionaries with exactly the fields needed by their kind:
``{"kind": "actor_message", "text": "..."}`` sends standalone work to the
actor mailbox; ``{"kind": "conversation_callback", "text": "..."}``
continues the owner conversation. ``once=True`` is normally used with ``at``.

Use ``await list_jobs(name_glob="...", status="active")`` to list this
actor's jobs, ``await find(job_id)`` to fetch one, ``await pause(job_id)`` or
``await resume(job_id)`` to control it, and ``await delete(job_id)`` to remove
it. These functions return ``CronJob`` objects except ``delete``. A
``CronJob`` exposes ``id``, ``name``, ``status``, ``schedule``, ``action``,
``next_run_at``, ``last_run_at``, and ``once``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from yb._daemon import daemon_url, request_json, task_owner


class CronJob:
    id: str
    name: str
    owner: str
    status: str
    schedule: dict[str, object]
    action: dict[str, object]
    next_run_at: str | None
    last_run_at: str | None
    once: bool

    def __init__(self, payload: dict[str, object], base_url: str) -> None:
        self.id = str(payload["id"])
        self.name = str(payload.get("name", ""))
        self.owner = str(payload.get("owner", ""))
        self.status = str(payload.get("status", "active"))  # type: ignore[assignment]
        schedule = payload.get("schedule")
        self.schedule = cast(dict[str, object], schedule) if isinstance(schedule, dict) else {}
        action = payload.get("action")
        self.action = cast(dict[str, object], action) if isinstance(action, dict) else {}
        next_run = payload.get("next_run_at")
        self.next_run_at = next_run if isinstance(next_run, str) else None
        last_run = payload.get("last_run_at")
        self.last_run_at = last_run if isinstance(last_run, str) else None
        self.once = bool(payload.get("once", False))
        self._base_url = base_url.rstrip("/")


async def list_jobs(name_glob: str = "", status: str = "") -> list[CronJob]:
    base_url = daemon_url()
    owner = task_owner()
    params: dict[str, str] = {"owner": owner}
    if name_glob:
        params["name_glob"] = name_glob
    if status:
        params["status"] = status
    payload = await request_json("GET", f"{base_url}/api/cron-jobs", params=params)
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [_job_from_payload(cast(dict[str, object], item), base_url) for item in items if isinstance(item, dict)]


async def find(job_id: str) -> CronJob:
    base_url = daemon_url()
    payload = await request_json("GET", f"{base_url}/api/cron-jobs/{job_id}")
    return _job_from_payload(payload, base_url)


async def add(
    name: str,
    *,
    timezone: str,
    cron: str | None = None,
    at: str | None = None,
    once: bool = False,
    action: dict[str, object],
) -> CronJob:
    if not timezone:
        raise ValueError("timezone is required")
    if bool(cron) == bool(at):
        raise ValueError("exactly one of cron or at is required")
    schedule: dict[str, object] = {"timezone": timezone}
    if cron is not None:
        schedule["kind"] = "cron"
        schedule["cron"] = cron
    else:
        schedule["kind"] = "at"
        schedule["at"] = _normalize_at(at or "", timezone)
    base_url = daemon_url()
    owner = task_owner()
    body: dict[str, object] = {
        "name": name,
        "owner": owner,
        "schedule": schedule,
        "action": action,
        "once": once,
    }
    payload = await request_json("POST", f"{base_url}/api/cron-jobs", json=body)
    return _job_from_payload(payload, base_url)


async def pause(job_id: str) -> CronJob:
    base_url = daemon_url()
    payload = await request_json("POST", f"{base_url}/api/cron-jobs/{job_id}/pause")
    return _job_from_payload(payload, base_url)


async def resume(job_id: str) -> CronJob:
    base_url = daemon_url()
    payload = await request_json("POST", f"{base_url}/api/cron-jobs/{job_id}/resume")
    return _job_from_payload(payload, base_url)


async def delete(job_id: str) -> None:
    base_url = daemon_url()
    await request_json("DELETE", f"{base_url}/api/cron-jobs/{job_id}")


def _job_from_payload(payload: dict[str, object], base_url: str) -> CronJob:
    return CronJob(payload, base_url=base_url)


_RELATIVE_AT = re.compile(r"^\+(?P<count>\d+)(?P<unit>[smhd])$")


def _normalize_at(at: str, timezone: str) -> str:
    match = _RELATIVE_AT.fullmatch(at.strip())
    if match is None:
        return at
    count = int(match.group("count"))
    unit = match.group("unit")
    delta = {
        "s": timedelta(seconds=count),
        "m": timedelta(minutes=count),
        "h": timedelta(hours=count),
        "d": timedelta(days=count),
    }[unit]
    local = datetime.now(ZoneInfo(timezone)) + delta
    return local.replace(tzinfo=None).isoformat(timespec="seconds")
