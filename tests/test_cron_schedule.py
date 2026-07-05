from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from yuubot.runtime.cron.models import CronSchedule, ReminderAction, ShellAction
from yuubot.runtime.cron.triggers import CronScheduleError, build_trigger, validate_schedule, validate_timezone


def test_validate_timezone_requires_iana_name() -> None:
    tz = validate_timezone("Asia/Shanghai")
    assert str(tz) == "Asia/Shanghai"


def test_validate_timezone_rejects_unknown() -> None:
    with pytest.raises(CronScheduleError):
        validate_timezone("Not/AZone")


def test_build_cron_trigger() -> None:
    trigger = build_trigger(CronSchedule(kind="cron", timezone="UTC", cron="0 9 * * mon-fri"))
    next_run = trigger.get_next_fire_time(None, datetime.now(UTC))
    assert next_run is not None


def test_build_at_trigger() -> None:
    local_at = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    trigger = build_trigger(CronSchedule(kind="at", timezone="UTC", at=local_at))
    next_run = trigger.get_next_fire_time(None, datetime.now(UTC))
    assert next_run is not None


def test_validate_schedule_rejects_missing_timezone_fields() -> None:
    with pytest.raises(CronScheduleError):
        validate_schedule(CronSchedule(kind="cron", timezone="UTC"))
    with pytest.raises(CronScheduleError):
        validate_schedule(CronSchedule(kind="at", timezone="UTC"))
