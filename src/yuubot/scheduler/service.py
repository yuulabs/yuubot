"""Wall-clock based cron scheduler with resume-aware catch-up throttling."""

from __future__ import annotations

import asyncio
from datetime import datetime
import heapq
import threading
import time
from typing import Protocol

from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from yuubot.config import Config
from yuubot.scheduler.core import (
    CatchupLimiter,
    DispatchRequest,
    ScheduleEntry,
    ScheduleSpec,
    advance_due_fire_time,
    detect_resume,
)

_DB_PREFIX = "dbtask-"


class _AgentRunner(Protocol):
    async def run_scheduled(
        self, task: str, ctx_id: int | None, *, agent_name: str = "main",
    ) -> None: ...


class Scheduler:
    def __init__(self, config: Config, agent_runner: _AgentRunner) -> None:
        self.config = config
        self.agent_runner = agent_runner
        self._entries: dict[str, ScheduleEntry] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._wakeup = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._deadline_heap: list[tuple[datetime, int, str, int]] = []
        self._deadline_seq = 0
        self._catchup_heap: list[tuple[float, int, DispatchRequest]] = []
        self._catchup_seq = 0
        self._catchup_limiter = CatchupLimiter(
            self.config.schedule.catchup_spacing_seconds
        )
        self._drift_thread: threading.Thread | None = None
        self._drift_stop = threading.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Scheduler already started")

        self._stopping = False
        self._started = asyncio.Event()
        self._wakeup = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._drift_stop = threading.Event()
        self._drift_thread = threading.Thread(
            target=self._watch_clock_drift,
            name="yuubot-scheduler-drift",
            daemon=True,
        )
        self._drift_thread.start()
        self._task = asyncio.create_task(self._run())
        await self._started.wait()

        if self._task.done():
            exc = self._task.exception()
            if exc:
                raise exc

    async def stop(self) -> None:
        self._stopping = True
        self._drift_stop.set()
        self._wakeup.set()

        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._drift_thread is not None:
            self._drift_thread.join(timeout=1.0)
            self._drift_thread = None

        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)

    async def reload(self) -> None:
        now = self._wall_now()
        async with self._lock:
            await self._sync_locked(now)
        self._wakeup.set()

    async def _run(self) -> None:
        try:
            now = self._wall_now()
            async with self._lock:
                await self._sync_locked(now)

            self._started.set()

            while not self._stopping:
                await self._tick(self._wall_now())

                async with self._lock:
                    timeout = self._next_wait_seconds_locked(self._wall_now())
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)
                    self._wakeup.clear()
                except asyncio.TimeoutError:
                    pass
        finally:
            self._entries.clear()
            self._deadline_heap.clear()
            self._catchup_heap.clear()
            if not self._started.is_set():
                self._started.set()

    async def _tick(self, wall_now: datetime) -> None:
        ready: list[DispatchRequest] = []
        async with self._lock:
            ready.extend(self._pop_due_deadlines_locked(wall_now))
            ready.extend(self._pop_ready_catchup_locked(wall_now))

        for request in ready:
            self._launch_dispatch(request)

    async def _sync_locked(self, wall_now: datetime) -> None:
        desired = await self._load_specs()
        next_entries: dict[str, ScheduleEntry] = {}

        for spec in desired:
            existing = self._entries.get(spec.id)
            if existing is not None and existing.spec == spec:
                next_entries[spec.id] = existing
                continue

            generation = 1 if existing is None else existing.generation + 1
            trigger = CronTrigger.from_crontab(spec.cron)
            next_entries[spec.id] = ScheduleEntry(
                spec=spec,
                trigger=trigger,
                next_fire_time=trigger.next(),
                generation=generation,
            )
            logger.info("Scheduled: {} @ {}", spec.id, spec.cron)

        removed_ids = set(self._entries) - set(next_entries)
        for schedule_id in removed_ids:
            logger.info("Removed schedule: {}", schedule_id)

        self._entries = next_entries
        self._rebuild_deadline_heap_locked()
        self._rebuild_catchup_heap_locked()

    async def _load_specs(self) -> list[ScheduleSpec]:
        from yuubot.core.models import ScheduledTask

        specs = [
            ScheduleSpec(
                id=f"cron-{job.task[:20]}",
                cron=job.cron,
                task=job.task,
                ctx_id=job.ctx_id,
            )
            for job in self.config.cron_jobs
            if job.cron and job.task
        ]

        db_tasks = await ScheduledTask.filter(enabled=True).all()
        for task in db_tasks:
            specs.append(
                ScheduleSpec(
                    id=f"{_DB_PREFIX}{task.id}",
                    cron=task.cron,
                    task=task.task,
                    ctx_id=task.ctx_id,
                    agent_name=task.agent,
                    once=task.once,
                    db_id=task.id,
                )
            )

        return specs

    def _rebuild_deadline_heap_locked(self) -> None:
        heap: list[tuple[datetime, int, str, int]] = []
        for entry in self._entries.values():
            if entry.next_fire_time is None:
                continue
            self._deadline_seq += 1
            heapq.heappush(
                heap,
                (
                    entry.next_fire_time,
                    self._deadline_seq,
                    entry.spec.id,
                    entry.generation,
                ),
            )

        self._deadline_heap = heap
        heapq.heapify(self._deadline_heap)

    def _rebuild_catchup_heap_locked(self) -> None:
        heap: list[tuple[float, int, DispatchRequest]] = []
        while self._catchup_heap:
            ready_ts, _, request = heapq.heappop(self._catchup_heap)
            entry = self._entries.get(request.schedule_id)
            if entry is None or entry.generation != request.generation:
                continue
            self._catchup_seq += 1
            heapq.heappush(heap, (ready_ts, self._catchup_seq, request))

        self._catchup_heap = heap
        heapq.heapify(self._catchup_heap)
        self._catchup_limiter.sync(
            max(item[0] for item in self._catchup_heap) if self._catchup_heap else None
        )

    def _push_deadline_locked(self, entry: ScheduleEntry) -> None:
        if entry.next_fire_time is None:
            return

        self._deadline_seq += 1
        heapq.heappush(
            self._deadline_heap,
            (
                entry.next_fire_time,
                self._deadline_seq,
                entry.spec.id,
                entry.generation,
            ),
        )

    def _prune_deadline_heap_locked(self) -> None:
        while self._deadline_heap:
            fire_time, _, schedule_id, generation = self._deadline_heap[0]
            entry = self._entries.get(schedule_id)
            if (
                entry is None
                or entry.generation != generation
                or entry.next_fire_time != fire_time
            ):
                heapq.heappop(self._deadline_heap)
                continue
            return

    def _prune_catchup_heap_locked(self) -> None:
        while self._catchup_heap:
            _, _, request = self._catchup_heap[0]
            entry = self._entries.get(request.schedule_id)
            if entry is None or entry.generation != request.generation:
                heapq.heappop(self._catchup_heap)
                continue
            return

    def _pop_due_deadlines_locked(self, wall_now: datetime) -> list[DispatchRequest]:
        ready: list[DispatchRequest] = []
        self._prune_deadline_heap_locked()

        while self._deadline_heap and self._deadline_heap[0][0] <= wall_now:
            fire_time, _, schedule_id, generation = heapq.heappop(self._deadline_heap)
            entry = self._entries.get(schedule_id)
            if (
                entry is None
                or entry.generation != generation
                or entry.next_fire_time != fire_time
            ):
                self._prune_deadline_heap_locked()
                continue

            due_fire_time, next_fire_time = advance_due_fire_time(
                entry.trigger,
                fire_time,
                wall_now,
                once=entry.spec.once,
            )
            entry.next_fire_time = next_fire_time
            self._push_deadline_locked(entry)

            if entry.running or entry.queued:
                entry.pending_fire_time = due_fire_time
                self._prune_deadline_heap_locked()
                continue

            request = self._build_request(entry, due_fire_time, wall_now)
            if request.delayed:
                entry.queued = True
                self._enqueue_catchup_locked(request, wall_now)
                self._prune_deadline_heap_locked()
                continue

            entry.running = True
            ready.append(request)
            self._prune_deadline_heap_locked()

        return ready

    def _pop_ready_catchup_locked(self, wall_now: datetime) -> list[DispatchRequest]:
        ready: list[DispatchRequest] = []
        now_ts = wall_now.timestamp()
        self._prune_catchup_heap_locked()

        while self._catchup_heap and self._catchup_heap[0][0] <= now_ts:
            _, _, request = heapq.heappop(self._catchup_heap)
            entry = self._entries.get(request.schedule_id)
            if entry is None or entry.generation != request.generation:
                self._prune_catchup_heap_locked()
                continue

            if entry.running:
                entry.pending_fire_time = request.scheduled_at
                entry.queued = False
                self._prune_catchup_heap_locked()
                continue

            entry.queued = False
            entry.running = True
            ready.append(request)
            self._prune_catchup_heap_locked()

        return ready

    def _build_request(
        self, entry: ScheduleEntry, scheduled_at: datetime, wall_now: datetime,
    ) -> DispatchRequest:
        delay_seconds = (wall_now - scheduled_at).total_seconds()
        return DispatchRequest(
            schedule_id=entry.spec.id,
            generation=entry.generation,
            task=entry.spec.task,
            ctx_id=entry.spec.ctx_id,
            agent_name=entry.spec.agent_name,
            once=entry.spec.once,
            db_id=entry.spec.db_id,
            scheduled_at=scheduled_at,
            delayed=delay_seconds > self.config.schedule.late_grace_seconds,
        )

    def _enqueue_catchup_locked(
        self, request: DispatchRequest, wall_now: datetime,
    ) -> None:
        ready_ts = self._catchup_limiter.reserve(wall_now)
        self._catchup_seq += 1
        heapq.heappush(self._catchup_heap, (ready_ts, self._catchup_seq, request))

    def _launch_dispatch(self, request: DispatchRequest) -> None:
        task = asyncio.create_task(self._dispatch(request))
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    async def _dispatch(self, request: DispatchRequest) -> None:
        try:
            logger.info(
                "Scheduled trigger: {} (ctx={}, agent={}, delayed={})",
                request.task,
                request.ctx_id,
                request.agent_name,
                request.delayed,
            )
            await self.agent_runner.run_scheduled(
                request.task,
                request.ctx_id,
                agent_name=request.agent_name,
            )
        except Exception:
            logger.exception("Scheduled agent failed: {}", request.task)
        finally:
            await self._finish_dispatch(request)

    async def _finish_dispatch(self, request: DispatchRequest) -> None:
        followup: DispatchRequest | None = None
        disable_db_id: int | None = None
        wall_now = self._wall_now()

        async with self._lock:
            entry = self._entries.get(request.schedule_id)
            if entry is None or entry.generation != request.generation:
                return

            entry.running = False
            entry.queued = False

            if request.once:
                disable_db_id = request.db_id
                self._entries.pop(request.schedule_id, None)
            elif entry.pending_fire_time is not None:
                scheduled_at = entry.pending_fire_time
                entry.pending_fire_time = None
                followup = self._build_request(entry, scheduled_at, wall_now)
                if followup.delayed:
                    entry.queued = True
                    self._enqueue_catchup_locked(followup, wall_now)
                    followup = None
                else:
                    entry.running = True

        if disable_db_id is not None:
            await self._disable_one_shot(disable_db_id)
        if followup is not None:
            self._launch_dispatch(followup)
        self._wakeup.set()

    async def _disable_one_shot(self, db_id: int) -> None:
        from yuubot.core.models import ScheduledTask

        try:
            obj = await ScheduledTask.get_or_none(id=db_id)
            if obj is not None:
                obj.enabled = False
                await obj.save()
                logger.info("One-shot task id={} auto-disabled", db_id)
        except Exception:
            logger.exception("Failed to auto-disable one-shot task id={}", db_id)

    def _next_wait_seconds_locked(self, wall_now: datetime) -> float | None:
        self._prune_deadline_heap_locked()
        self._prune_catchup_heap_locked()

        waits: list[float] = []
        if self._deadline_heap:
            waits.append(max(0.0, (self._deadline_heap[0][0] - wall_now).total_seconds()))
        if self._catchup_heap:
            waits.append(max(0.0, self._catchup_heap[0][0] - wall_now.timestamp()))
        if not waits:
            return None

        return min(waits)

    def _wall_now(self) -> datetime:
        return datetime.now().astimezone()

    def _monotonic_now(self) -> float:
        return time.monotonic()

    def _watch_clock_drift(self) -> None:
        previous_wall = self._wall_now()
        previous_monotonic = self._monotonic_now()
        interval = max(0.1, self.config.schedule.tick_seconds)

        while not self._drift_stop.wait(interval):
            wall_now = self._wall_now()
            monotonic_now = self._monotonic_now()
            if detect_resume(
                previous_wall,
                previous_monotonic,
                wall_now,
                monotonic_now,
                threshold_seconds=self.config.schedule.resume_threshold_seconds,
            ):
                logger.info(
                    "Scheduler clock drift detected; forcing wall-clock wakeup"
                )
                loop = self._loop
                if loop is not None and not self._stopping:
                    loop.call_soon_threadsafe(self._wakeup.set)

            previous_wall = wall_now
            previous_monotonic = monotonic_now
