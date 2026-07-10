"""Persistent cron job storage."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING

from attrs import define

from ...util.time import utc_now_iso

from .models import (
    CronAction,
    CronJob,
    CronJobStatus,
    CronSchedule,
    decode_cron_job,
    encode_cron_job,
    lifecycle_once_for_schedule,
    new_cron_job_id,
)
from .triggers import validate_schedule

if TYPE_CHECKING:
    from ...db import Database

@define
class CronJobStore:
    _db: Database

    async def list_jobs(
        self,
        owner: str | None = None,
        status: CronJobStatus | None = None,
        name_glob: str = "",
    ) -> list[CronJob]:
        cursor = await self._db.execute("select payload from app_cron_jobs order by id")
        rows = await cursor.fetchall()
        items = [decode_cron_job(payload) for payload, in rows]
        if owner is not None:
            items = [job for job in items if job.owner == owner]
        if status is not None:
            items = [job for job in items if job.status == status]
        if name_glob:
            items = [job for job in items if fnmatch(job.name, name_glob)]
        return items

    async def get(self, job_id: str) -> CronJob:
        cursor = await self._db.execute("select payload from app_cron_jobs where id = ?", (job_id,))
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(job_id)
        return decode_cron_job(row[0])

    async def put(self, job: CronJob) -> CronJob:
        validate_schedule(job.schedule)
        timestamp = utc_now_iso()
        created_at = job.created_at or timestamp
        stored = CronJob(
            id=job.id,
            owner=job.owner,
            name=job.name,
            schedule=job.schedule,
            action=job.action,
            status=job.status,
            next_run_at=job.next_run_at,
            last_run_at=job.last_run_at,
            once=lifecycle_once_for_schedule(job.schedule),
            created_at=created_at,
            updated_at=timestamp,
        )
        await self._db.execute(
            """
            insert into app_cron_jobs (id, payload, created_at, updated_at)
            values (?, ?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (stored.id, encode_cron_job(stored), stored.created_at, stored.updated_at),
        )
        await self._db.commit()
        return stored

    async def delete(self, job_id: str) -> bool:
        cursor = await self._db.execute("delete from app_cron_jobs where id = ?", (job_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    def new_id(self) -> str:
        return new_cron_job_id()

    async def build_new(
        self,
        owner: str,
        name: str,
        schedule: CronSchedule,
        action: CronAction,
        once: bool = False,
        status: CronJobStatus = "active",
    ) -> CronJob:
        validate_schedule(schedule)
        timestamp = utc_now_iso()
        return CronJob(
            id=self.new_id(),
            owner=owner,
            name=name,
            schedule=schedule,
            action=action,
            status=status,
            once=lifecycle_once_for_schedule(schedule),
            created_at=timestamp,
            updated_at=timestamp,
        )
