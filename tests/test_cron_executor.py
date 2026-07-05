from __future__ import annotations

import pytest

from yuubot.app import Yuubot
from yuubot.runtime.cron.models import CronSchedule, ReminderAction


@pytest.mark.asyncio
async def test_cron_executor_emits_notification_event(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    app.runtime.resolve_actor_workspace = app._actor_workspace_path
    try:
        job = await app.runtime.cron_jobs.build_new(
            owner="actor:amy:conv:c1",
            name="notify",
            schedule=CronSchedule(kind="at", timezone="UTC", at="2099-01-01T09:00:00"),
            action=ReminderAction(title="Ping", body="Reminder body", channels=()),
            once=True,
        )
        await app.runtime.cron_jobs.put(job)
        await app.runtime.cron_executor.run(job.id)
        kinds = [event.kind for event in app.runtime.eventbus.events]
        assert "notification.delivered" in kinds
        assert "cron.finished" in kinds
        stored = await app.runtime.cron_jobs.get(job.id)
        assert stored.status == "completed"
    finally:
        await app.shutdown()
