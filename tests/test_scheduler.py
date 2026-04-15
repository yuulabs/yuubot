from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import time

from apscheduler.triggers.cron import CronTrigger
import pytest

from yuubot.config import Config, DatabaseConfig, ScheduleConfig
from yuubot.core.models import ScheduledTask
from yuubot.scheduler.core import CatchupLimiter, advance_due_fire_time, detect_resume
from yuubot.scheduler.service import Scheduler


class FakeAgentRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, str, float]] = []
        self._gate = asyncio.Event()
        self._gate.set()

    def block(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    async def run_scheduled(
        self, task: str, ctx_id: int | None, *, agent_name: str = "main",
    ) -> None:
        self.calls.append((task, ctx_id, agent_name, time.monotonic()))
        await self._gate.wait()


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def test_advance_due_fire_time_coalesces_missed_runs() -> None:
    trigger = CronTrigger.from_crontab("*/5 * * * *")
    first = trigger.next()
    assert first is not None

    now = first + timedelta(minutes=16)
    fire_time, next_fire_time = advance_due_fire_time(
        trigger,
        first,
        now,
        once=False,
    )

    assert fire_time == first + timedelta(minutes=15)
    assert next_fire_time == first + timedelta(minutes=20)


def test_detect_resume_uses_wall_clock_gap() -> None:
    previous_wall = datetime.now().astimezone()
    previous_monotonic = 10.0
    wall_now = previous_wall + timedelta(minutes=30)
    monotonic_now = previous_monotonic + 5.0

    assert detect_resume(
        previous_wall,
        previous_monotonic,
        wall_now,
        monotonic_now,
        threshold_seconds=30.0,
    )


def test_catchup_limiter_staggers_delayed_runs() -> None:
    limiter = CatchupLimiter(3.0)
    now = datetime.now().astimezone()

    first = limiter.reserve(now)
    second = limiter.reserve(now)
    third = limiter.reserve(now + timedelta(seconds=1))

    assert second - first == pytest.approx(3.0)
    assert third - second == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_scheduler_disables_one_shot_tasks_after_delayed_run(db) -> None:
    runner = FakeAgentRunner()
    scheduler = Scheduler(
        Config(
            database=DatabaseConfig(path=db),
            schedule=ScheduleConfig(
                tick_seconds=0.05,
                late_grace_seconds=0.0,
                catchup_spacing_seconds=0.05,
                resume_threshold_seconds=5.0,
            ),
        ),
        runner,
    )

    task = await ScheduledTask.create(
        cron="0 0 1 1 *",
        task="send delayed reminder",
        agent="main",
        ctx_id=42,
        once=True,
    )

    await scheduler.start()
    try:
        async with scheduler._lock:
            entry = scheduler._entries[f"dbtask-{task.id}"]
            entry.next_fire_time = scheduler._wall_now() - timedelta(seconds=5)
            scheduler._push_deadline_locked(entry)

        scheduler._wakeup.set()
        await _wait_until(lambda: len(runner.calls) == 1)

        refreshed = await ScheduledTask.get(id=task.id)
        assert refreshed.enabled is False
        assert runner.calls[0][:3] == ("send delayed reminder", 42, "main")
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_staggers_multiple_delayed_tasks(db) -> None:
    runner = FakeAgentRunner()
    scheduler = Scheduler(
        Config(
            database=DatabaseConfig(path=db),
            schedule=ScheduleConfig(
                tick_seconds=0.02,
                late_grace_seconds=0.0,
                catchup_spacing_seconds=0.10,
                resume_threshold_seconds=5.0,
            ),
        ),
        runner,
    )

    first = await ScheduledTask.create(
        cron="0 0 1 1 *",
        task="first backlog task",
        agent="main",
        once=True,
    )
    second = await ScheduledTask.create(
        cron="0 0 1 1 *",
        task="second backlog task",
        agent="main",
        once=True,
    )

    await scheduler.start()
    try:
        async with scheduler._lock:
            first_entry = scheduler._entries[f"dbtask-{first.id}"]
            first_entry.next_fire_time = scheduler._wall_now() - timedelta(seconds=5)
            scheduler._push_deadline_locked(first_entry)

            second_entry = scheduler._entries[f"dbtask-{second.id}"]
            second_entry.next_fire_time = scheduler._wall_now() - timedelta(seconds=5)
            scheduler._push_deadline_locked(second_entry)

        scheduler._wakeup.set()
        await _wait_until(lambda: len(runner.calls) == 2, timeout=1.5)

        first_call = runner.calls[0][3]
        second_call = runner.calls[1][3]
        assert second_call - first_call >= 0.08
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_waits_for_deadline_not_tick_interval(db) -> None:
    runner = FakeAgentRunner()
    scheduler = Scheduler(
        Config(
            database=DatabaseConfig(path=db),
            schedule=ScheduleConfig(
                tick_seconds=10.0,
                late_grace_seconds=0.5,
                catchup_spacing_seconds=0.05,
                resume_threshold_seconds=5.0,
            ),
        ),
        runner,
    )

    task = await ScheduledTask.create(
        cron="0 0 1 1 *",
        task="deadline driven task",
        agent="main",
        ctx_id=7,
        once=True,
    )

    await scheduler.start()
    try:
        start = time.monotonic()
        async with scheduler._lock:
            entry = scheduler._entries[f"dbtask-{task.id}"]
            entry.next_fire_time = scheduler._wall_now() + timedelta(seconds=0.05)
            scheduler._push_deadline_locked(entry)

        scheduler._wakeup.set()
        await _wait_until(lambda: len(runner.calls) == 1, timeout=1.0)

        assert runner.calls[0][0] == "deadline driven task"
        assert runner.calls[0][3] - start < 0.5
    finally:
        await scheduler.stop()
