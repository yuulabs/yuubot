"""Cron scheduler for active mode tasks."""

import asyncio
from typing import Protocol

from apscheduler import AsyncScheduler
from apscheduler.triggers.cron import CronTrigger

from yuubot.config import Config

from loguru import logger

_DB_PREFIX = "dbtask-"


class _AgentRunner(Protocol):
    async def run_scheduled(
        self, task: str, ctx_id: int | None, *, agent_name: str = "main",
    ) -> None: ...


class Scheduler:
    def __init__(self, config: Config, agent_runner: _AgentRunner) -> None:
        self.config = config
        self.agent_runner = agent_runner
        self._scheduler: AsyncScheduler | None = None
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Scheduler already started")

        self._started = asyncio.Event()
        self._task = asyncio.create_task(self._run())
        await self._started.wait()

        if self._task.done():
            exc = self._task.exception()
            if exc:
                raise exc

    async def stop(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.stop()
            await self._scheduler.wait_until_stopped()

        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def reload(self) -> None:
        """Re-sync DB tasks into the running scheduler."""
        if self._scheduler is None:
            return
        await self._sync_db_schedules()

    async def _trigger(self, task: str, ctx_id: int | None) -> None:
        logger.info("Cron trigger: %s (ctx=%s)", task, ctx_id)
        await self.agent_runner.run_scheduled(task, ctx_id)

    async def _trigger_dynamic(
        self, db_id: int, task: str, ctx_id: int | None, agent_name: str, once: bool,
    ) -> None:
        logger.info(
            "DB trigger id=%d: %s (ctx=%s, agent=%s, once=%s)",
            db_id, task, ctx_id, agent_name, once,
        )
        await self.agent_runner.run_scheduled(task, ctx_id, agent_name=agent_name)
        if once:
            try:
                from yuubot.core.models import ScheduledTask

                obj = await ScheduledTask.get_or_none(id=db_id)
                if obj is not None:
                    obj.enabled = False
                    await obj.save()
                    logger.info("One-shot task id=%d auto-disabled", db_id)
                    # Remove from scheduler
                    if self._scheduler is not None:
                        job_id = f"{_DB_PREFIX}{db_id}"
                        try:
                            await self._scheduler.remove_schedule(job_id)
                        except Exception:
                            pass
            except Exception:
                logger.exception("Failed to auto-disable one-shot task id=%d", db_id)

    async def _sync_db_schedules(self) -> None:
        """Full diff between DB enabled tasks and scheduler dynamic jobs."""
        if self._scheduler is None:
            return

        from yuubot.core.models import ScheduledTask

        db_tasks = await ScheduledTask.filter(enabled=True).all()
        desired: dict[str, ScheduledTask] = {}
        for t in db_tasks:
            desired[f"{_DB_PREFIX}{t.id}"] = t

        # Get current dynamic schedules
        current_ids: set[str] = set()
        for schedule in await self._scheduler.get_schedules():
            if schedule.id.startswith(_DB_PREFIX):
                current_ids.add(schedule.id)

        desired_ids = set(desired.keys())

        # Remove stale
        for job_id in current_ids - desired_ids:
            await self._scheduler.remove_schedule(job_id)
            logger.info("Removed stale DB schedule: %s", job_id)

        # Add new
        for job_id in desired_ids - current_ids:
            t = desired[job_id]
            await self._scheduler.add_schedule(
                self._trigger_dynamic,
                CronTrigger.from_crontab(t.cron),
                args=[t.id, t.task, t.ctx_id, t.agent, t.once],
                id=job_id,
            )
            logger.info("Added DB schedule: %s (%s)", job_id, t.cron)

        # Update changed (remove + re-add)
        for job_id in desired_ids & current_ids:
            t = desired[job_id]
            await self._scheduler.remove_schedule(job_id)
            await self._scheduler.add_schedule(
                self._trigger_dynamic,
                CronTrigger.from_crontab(t.cron),
                args=[t.id, t.task, t.ctx_id, t.agent, t.once],
                id=job_id,
            )

    async def _run(self) -> None:
        jobs = [job for job in self.config.cron_jobs if job.cron and job.task]

        try:
            async with AsyncScheduler() as scheduler:
                self._scheduler = scheduler

                for job in jobs:
                    await scheduler.add_schedule(
                        self._trigger,
                        CronTrigger.from_crontab(job.cron),
                        args=[job.task, job.ctx_id],
                        id=f"cron-{job.task[:20]}",
                    )
                    logger.info("Scheduled: %s @ %s", job.task, job.cron)

                await scheduler.start_in_background()
                logger.info("Scheduler started (%d config jobs)", len(jobs))

                await self._sync_db_schedules()
                self._started.set()

                await scheduler.wait_until_stopped()
        finally:
            self._scheduler = None
            if not self._started.is_set():
                self._started.set()
