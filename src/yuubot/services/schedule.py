"""Schedule domain service for DB-backed cron tasks."""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any

import attrs
import httpx
from apscheduler.triggers.cron import CronTrigger

from yuubot.config import Config
from yuubot.core.models import ScheduledTask
from yuubot.services.base import InvalidScope, YuubotServiceError


def _is_master(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("bot_kind", "")).lower() == "master"


def _int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return default
    return default


def _validate_cron(cron: str) -> None:
    CronTrigger.from_crontab(cron)


def _expand_field(field: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            range_part, step_s = part.split("/", 1)
            step = int(step_s)
            if range_part == "*":
                start, end = lo, hi
            elif "-" in range_part:
                a, b = range_part.split("-", 1)
                start, end = int(a), int(b)
            else:
                start, end = int(range_part), hi
            values.update(range(start, end + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            values.update(range(int(a), int(b) + 1))
        elif part == "*":
            values.update(range(lo, hi + 1))
        else:
            values.add(int(part))
    return values


def _is_long_cycle(cron: str) -> bool:
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError(f"invalid cron expression: {cron}")
    return len(_expand_field(parts[3], 1, 12)) < 12


def _task_dict(task: ScheduledTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "cron": task.cron,
        "task": task.task,
        "agent": task.agent,
        "ctx_id": task.ctx_id,
        "created_by": task.created_by,
        "enabled": task.enabled,
        "once": task.once,
        "created_at": task.created_at.isoformat() if task.created_at else "",
    }


@attrs.define
class ScheduleService:
    config: Config | None = None

    async def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        cron = str(payload.get("cron", "") or "").strip()
        task_text = str(payload.get("task", "") or "").strip()
        if not cron or not task_text:
            raise YuubotServiceError("cron and task are required")
        _validate_cron(cron)
        once = bool(payload.get("once", True))
        recurring = bool(payload.get("recurring", False))
        if recurring:
            once = False
        ctx_id = _int(payload.get("target_ctx_id") or payload.get("ctx_id")) or None
        current_ctx = _int(payload.get("ctx_id")) or None
        if ctx_id != current_ctx and not _is_master(payload):
            raise InvalidScope("group agents may only schedule the current context")
        agent = str(payload.get("agent", payload.get("agent_name", "yuu")) or "yuu")
        if self.config is not None and not once and _is_long_cycle(cron):
            existing = await ScheduledTask.filter(enabled=True, once=False).all()
            long_count = sum(1 for item in existing if _is_long_cycle(item.cron))
            if long_count >= self.config.schedule.max_long_cycle:
                raise YuubotServiceError(
                    f"长周期定时任务已达上限 ({self.config.schedule.max_long_cycle})"
                )
        created_by = str(payload.get("character_name", payload.get("agent_name", "")) or payload.get("user_id", ""))
        task = await ScheduledTask.create(
            cron=cron,
            task=task_text,
            agent=agent,
            ctx_id=ctx_id,
            created_by=created_by,
            enabled=True,
            once=once,
        )
        await self._notify_reload()
        return _task_dict(task)

    async def list(self, payload: Mapping[str, Any]) -> builtins.list[dict[str, Any]]:
        show_all = bool(payload.get("all", False))
        filters: dict[str, Any] = {} if show_all else {"enabled": True}
        if not _is_master(payload):
            filters["ctx_id"] = _int(payload.get("ctx_id")) or None
        query = ScheduledTask.filter(**filters) if filters else ScheduledTask.all()
        tasks = await query.order_by("id")
        return [_task_dict(task) for task in tasks]

    async def cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _int(payload.get("schedule_id", payload.get("id")))
        if not task_id:
            raise YuubotServiceError("schedule_id is required")
        task = await ScheduledTask.get_or_none(id=task_id)
        if task is None:
            return {"status": "not_found", "id": task_id}
        if task.ctx_id != (_int(payload.get("ctx_id")) or None) and not _is_master(payload):
            raise InvalidScope("group agents may only cancel current-context schedules")
        task.enabled = False
        await task.save()
        await self._notify_reload()
        return {"status": "cancelled", "id": task_id}

    async def update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _int(payload.get("schedule_id", payload.get("id")))
        if not task_id:
            raise YuubotServiceError("schedule_id is required")
        task = await ScheduledTask.get_or_none(id=task_id)
        if task is None:
            return {"status": "not_found", "id": task_id}
        if task.ctx_id != (_int(payload.get("ctx_id")) or None) and not _is_master(payload):
            raise InvalidScope("group agents may only update current-context schedules")
        if payload.get("cron"):
            cron = str(payload["cron"])
            _validate_cron(cron)
            task.cron = cron
        if payload.get("task"):
            task.task = str(payload["task"])
        if payload.get("agent"):
            task.agent = str(payload["agent"])
        if "enabled" in payload:
            task.enabled = bool(payload["enabled"])
        if "once" in payload:
            task.once = bool(payload["once"])
        await task.save()
        await self._notify_reload()
        return {"status": "updated", "schedule": _task_dict(task)}

    async def _notify_reload(self) -> None:
        if self.config is None:
            return
        api = f"http://{self.config.daemon.api.host}:{self.config.daemon.api.port}"
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                await client.post(f"{api}/schedule/reload")
        except Exception:
            return
