from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import yb.tasks
from yb.tasks.cron import _normalize_at
from yuubot.runtime.cron.models import CronSchedule
from yuubot.runtime.cron.triggers import CronScheduleError, build_trigger, validate_schedule, validate_timezone


def test_validate_timezone_requires_iana_name() -> None:
    tz = validate_timezone("Asia/Shanghai")
    assert str(tz) == "Asia/Shanghai"


def test_validate_timezone_rejects_unknown() -> None:
    with pytest.raises(CronScheduleError):
        validate_timezone("Not/AZone")


def test_build_cron_trigger() -> None:
    trigger = build_trigger(CronSchedule("cron", "UTC", "0 9 * * mon-fri"))
    next_run = trigger.get_next_fire_time(None, datetime.now(UTC))
    assert next_run is not None


def test_build_at_trigger() -> None:
    local_at = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    trigger = build_trigger(CronSchedule("at", "UTC", at=local_at))
    next_run = trigger.get_next_fire_time(None, datetime.now(UTC))
    assert next_run is not None


def test_validate_schedule_rejects_missing_timezone_fields() -> None:
    with pytest.raises(CronScheduleError):
        validate_schedule(CronSchedule("cron", "UTC"))
    with pytest.raises(CronScheduleError):
        validate_schedule(CronSchedule("at", "UTC"))


def test_tasks_package_exposes_cron_facade() -> None:
    assert yb.tasks.cron.add is not None


def test_cron_facade_accepts_short_relative_at() -> None:
    normalized = _normalize_at("+1m", "UTC")
    parsed = datetime.fromisoformat(normalized)
    assert parsed.tzinfo is None
    assert 0 < (parsed - datetime.now(UTC).replace(tzinfo=None)).total_seconds() <= 90
