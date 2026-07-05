"""Schedule validation and APScheduler trigger construction."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .models import CronSchedule


class CronScheduleError(ValueError):
    pass


def validate_timezone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise CronScheduleError(f"unknown timezone: {timezone}") from exc


def parse_local_at(at: str, timezone: str) -> datetime:
    validate_timezone(timezone)
    try:
        local = datetime.fromisoformat(at)
    except ValueError as exc:
        raise CronScheduleError(f"invalid at datetime: {at}") from exc
    if local.tzinfo is not None:
        raise CronScheduleError("at must be a timezone-naive local datetime")
    return local.replace(tzinfo=ZoneInfo(timezone))


def build_trigger(schedule: CronSchedule) -> BaseTrigger:
    tz = validate_timezone(schedule.timezone)
    if schedule.kind == "cron":
        if not schedule.cron:
            raise CronScheduleError("cron expression is required for cron schedule")
        return CronTrigger.from_crontab(schedule.cron, timezone=tz)
    if schedule.kind == "at":
        if not schedule.at:
            raise CronScheduleError("at is required for at schedule")
        return DateTrigger(run_date=parse_local_at(schedule.at, schedule.timezone))
    raise CronScheduleError(f"unknown schedule kind: {schedule.kind}")


def validate_schedule(schedule: CronSchedule) -> None:
    validate_timezone(schedule.timezone)
    if schedule.kind == "cron":
        if not schedule.cron:
            raise CronScheduleError("cron expression is required")
        if schedule.at:
            raise CronScheduleError("at must not be set for cron schedule")
        build_trigger(schedule)
        return
    if schedule.kind == "at":
        if not schedule.at:
            raise CronScheduleError("at is required")
        if schedule.cron:
            raise CronScheduleError("cron must not be set for at schedule")
        build_trigger(schedule)
        return
    raise CronScheduleError(f"unknown schedule kind: {schedule.kind}")
