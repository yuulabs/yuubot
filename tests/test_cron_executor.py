from __future__ import annotations

import pytest

from yuubot.app import Yuubot
from yuubot.runtime.cron.models import (
    ActorMessageAction,
    ConversationCallbackAction,
    CronSchedule,
    ReminderAction,
    WakeupAction,
)
from yuubot.runtime.cron.scheduler import CRON_MISFIRE_GRACE_TIME_S
from yuubot.runtime.wakeup import WakeupPayload, WakeupTarget


class CapturingWakeup:
    def __init__(self) -> None:
        self.deliveries: list[tuple[WakeupTarget, WakeupPayload]] = []

    async def deliver(self, target: WakeupTarget, payload: WakeupPayload) -> None:
        self.deliveries.append((target, payload))


@pytest.mark.asyncio
async def test_cron_executor_emits_notification_event(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    app.runtime.resolve_actor_workspace = app.actor_workspace_path
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


@pytest.mark.asyncio
async def test_cron_executor_actor_message_uses_actor_inbound_without_conversation(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    wakeup = CapturingWakeup()
    app.runtime.wakeup = wakeup  # type: ignore[assignment]
    app.runtime.resolve_actor_workspace = lambda _actor_id: tmp_path / "workspace"
    try:
        job = await app.runtime.cron_jobs.build_new(
            owner="actor:amy:conv:owner-conv",
            name="daily",
            schedule=CronSchedule(kind="at", timezone="UTC", at="2099-01-01T09:00:00"),
            action=ActorMessageAction(text="run daily"),
            once=True,
        )
        await app.runtime.cron_jobs.put(job)
        await app.runtime.cron_executor.run(job.id)

        [(target, payload)] = wakeup.deliveries
        assert target.kind == "actor_inbound"
        assert target.actor_id == "amy"
        assert target.conversation_id is None
        assert payload.source["cron_delivery"] == "actor_message"
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_cron_executor_conversation_callback_uses_owner_conversation(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    wakeup = CapturingWakeup()
    app.runtime.wakeup = wakeup  # type: ignore[assignment]
    app.runtime.resolve_actor_workspace = lambda _actor_id: tmp_path / "workspace"
    try:
        job = await app.runtime.cron_jobs.build_new(
            owner="actor:amy:conv:owner-conv",
            name="callback",
            schedule=CronSchedule(kind="at", timezone="UTC", at="2099-01-01T09:00:00"),
            action=ConversationCallbackAction(text="continue here"),
            once=True,
        )
        await app.runtime.cron_jobs.put(job)
        await app.runtime.cron_executor.run(job.id)

        [(target, payload)] = wakeup.deliveries
        assert target.kind == "conversation_callback"
        assert target.actor_id == "amy"
        assert target.conversation_id == "owner-conv"
        assert payload.source["cron_delivery"] == "conversation_callback"
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_cron_executor_legacy_wakeup_is_actor_message(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    wakeup = CapturingWakeup()
    app.runtime.wakeup = wakeup  # type: ignore[assignment]
    app.runtime.resolve_actor_workspace = lambda _actor_id: tmp_path / "workspace"
    try:
        job = await app.runtime.cron_jobs.build_new(
            owner="actor:amy:conv:owner-conv",
            name="legacy",
            schedule=CronSchedule(kind="at", timezone="UTC", at="2099-01-01T09:00:00"),
            action=WakeupAction(text="old wake", conversation_id="ignored-conv"),
            once=True,
        )
        await app.runtime.cron_jobs.put(job)
        await app.runtime.cron_executor.run(job.id)

        [(target, payload)] = wakeup.deliveries
        assert target.kind == "actor_inbound"
        assert target.actor_id == "amy"
        assert target.conversation_id is None
        assert payload.source["cron_delivery"] == "actor_message"
        assert payload.source["cron_legacy_kind"] == "wakeup"
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_cron_scheduler_allows_short_runtime_delay(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    try:
        app.runtime.cron.start()
        job = await app.runtime.cron_jobs.build_new(
            owner="actor:amy:conv:c1",
            name="notify",
            schedule=CronSchedule(kind="at", timezone="UTC", at="2099-01-01T09:00:00"),
            action=ReminderAction(title="Ping", body="Reminder body", channels=()),
            once=True,
        )

        stored = await app.runtime.cron.register(job)
        scheduled = app.runtime.cron._scheduler.get_job(stored.id)

        assert scheduled is not None
        assert scheduled.misfire_grace_time == CRON_MISFIRE_GRACE_TIME_S
        assert scheduled.misfire_grace_time > 1
    finally:
        await app.shutdown()
