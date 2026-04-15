"""Core scheduling policies and in-memory state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger


@dataclass(frozen=True)
class ScheduleSpec:
    id: str
    cron: str
    task: str
    ctx_id: int | None
    agent_name: str = "main"
    once: bool = False
    db_id: int | None = None


@dataclass
class ScheduleEntry:
    spec: ScheduleSpec
    trigger: CronTrigger
    next_fire_time: datetime | None
    generation: int = 1
    running: bool = False
    queued: bool = False
    pending_fire_time: datetime | None = None


@dataclass(frozen=True)
class DispatchRequest:
    schedule_id: str
    generation: int
    task: str
    ctx_id: int | None
    agent_name: str
    once: bool
    db_id: int | None
    scheduled_at: datetime
    delayed: bool


def advance_due_fire_time(
    trigger: CronTrigger,
    next_fire_time: datetime,
    now: datetime,
    *,
    once: bool,
) -> tuple[datetime, datetime | None]:
    """Coalesce missed fire times into a single eventual execution."""
    fire_time = next_fire_time
    if once:
        return fire_time, None

    while True:
        candidate = trigger.next()
        if candidate is None or candidate > now:
            return fire_time, candidate
        fire_time = candidate


def detect_resume(
    previous_wall: datetime | None,
    previous_monotonic: float | None,
    wall_now: datetime,
    monotonic_now: float,
    *,
    threshold_seconds: float,
) -> bool:
    """Detect suspend/resume or large wall-clock jumps."""
    if previous_wall is None or previous_monotonic is None:
        return False

    wall_elapsed = (wall_now - previous_wall).total_seconds()
    monotonic_elapsed = monotonic_now - previous_monotonic
    return wall_elapsed - monotonic_elapsed >= threshold_seconds


class CatchupLimiter:
    """Reserve staggered wall-clock slots for delayed executions."""

    def __init__(self, spacing_seconds: float) -> None:
        self._spacing_seconds = max(0.0, spacing_seconds)
        self._next_ready_ts = 0.0

    def reserve(self, wall_now: datetime) -> float:
        now_ts = wall_now.timestamp()
        ready_ts = max(now_ts, self._next_ready_ts)
        self._next_ready_ts = ready_ts + self._spacing_seconds
        return ready_ts

    def sync(self, ready_ts: float | None) -> None:
        if ready_ts is None:
            self._next_ready_ts = 0.0
        else:
            self._next_ready_ts = ready_ts + self._spacing_seconds
