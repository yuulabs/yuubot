"""APScheduler-backed cron job scheduler."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from attrs import define, field

from .models import CronJob, CronJobStatus
from .triggers import build_trigger

if TYPE_CHECKING:
    from ..core import Runtime
    from .executor import CronExecutor
    from .store import CronJobStore

_log = logging.getLogger(__name__)

CRON_MISFIRE_GRACE_TIME_S = 60


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _next_run_iso(scheduler: AsyncIOScheduler, job_id: str) -> str | None:
    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.astimezone(UTC).isoformat()


@define
class CronJobScheduler:
    _runtime: Runtime
    _store: CronJobStore
    _executor: CronExecutor
    _scheduler: AsyncIOScheduler = field(factory=AsyncIOScheduler)
    _started: bool = field(default=False, init=False)

    def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True

    async def sync_from_store(self) -> None:
        for job in await self._store.list_jobs(status="active"):
            self._schedule(job)

    def shutdown(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False

    def next_run_at(self, job_id: str) -> str | None:
        return _next_run_iso(self._scheduler, job_id)

    def _schedule(self, job: CronJob) -> None:
        if job.status != "active":
            return
        trigger = build_trigger(job.schedule)
        self._scheduler.add_job(
            self._executor.run,
            trigger=trigger,
            id=job.id,
            args=[job.id],
            replace_existing=True,
            misfire_grace_time=CRON_MISFIRE_GRACE_TIME_S,
            coalesce=True,
            max_instances=1,
        )

    def unschedule(self, job_id: str) -> None:
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

    async def register(self, job: CronJob) -> CronJob:
        stored = await self._store.put(job)
        if stored.status == "active":
            self._schedule(stored)
            next_run = self.next_run_at(stored.id)
            if next_run != stored.next_run_at:
                stored = await self._store.put(
                    CronJob(
                        id=stored.id,
                        owner=stored.owner,
                        name=stored.name,
                        schedule=stored.schedule,
                        action=stored.action,
                        status=stored.status,
                        next_run_at=next_run,
                        last_run_at=stored.last_run_at,
                        once=stored.once,
                        created_at=stored.created_at,
                        updated_at=_now_iso(),
                    )
                )
        else:
            self.unschedule(stored.id)
        return stored

    async def pause(self, job_id: str) -> CronJob:
        job = await self._store.get(job_id)
        self.unschedule(job_id)
        return await self._store.put(
            CronJob(
                id=job.id,
                owner=job.owner,
                name=job.name,
                schedule=job.schedule,
                action=job.action,
                status="paused",
                next_run_at=None,
                last_run_at=job.last_run_at,
                once=job.once,
                created_at=job.created_at,
                updated_at=_now_iso(),
            )
        )

    async def resume(self, job_id: str) -> CronJob:
        job = await self._store.get(job_id)
        resumed = CronJob(
            id=job.id,
            owner=job.owner,
            name=job.name,
            schedule=job.schedule,
            action=job.action,
            status="active",
            next_run_at=job.next_run_at,
            last_run_at=job.last_run_at,
            once=job.once,
            created_at=job.created_at,
            updated_at=_now_iso(),
        )
        return await self.register(resumed)

    async def delete(self, job_id: str) -> bool:
        self.unschedule(job_id)
        return await self._store.delete(job_id)

    async def pause_for_owner_prefix(self, owner_prefix: str) -> None:
        for job in await self._store.list_jobs():
            if not job.owner.startswith(owner_prefix):
                continue
            if job.status != "active":
                continue
            await self.pause(job.id)
