"""Cron job data models."""

from __future__ import annotations

import uuid
from typing import Literal

import msgspec

CronJobStatus = Literal["active", "paused", "completed", "cancelled"]
ScheduleKind = Literal["cron", "at"]
ActionKind = Literal["shell", "wakeup", "actor_message", "conversation_callback", "reminder"]


class CronSchedule(msgspec.Struct, frozen=True):
    kind: ScheduleKind
    timezone: str
    cron: str | None = None
    at: str | None = None


class NotificationChannel(msgspec.Struct, frozen=True):
    kind: str
    config: dict[str, object] = msgspec.field(default_factory=dict)


class ShellAction(msgspec.Struct, frozen=True, tag="shell", tag_field="kind"):
    name: str
    shell: str
    intro: str


class WakeupAction(msgspec.Struct, frozen=True, tag="wakeup", tag_field="kind"):
    text: str
    conversation_id: str | None = None


class ActorMessageAction(msgspec.Struct, frozen=True, tag="actor_message", tag_field="kind"):
    text: str


class ConversationCallbackAction(msgspec.Struct, frozen=True, tag="conversation_callback", tag_field="kind"):
    text: str


class ReminderAction(msgspec.Struct, frozen=True, tag="reminder", tag_field="kind"):
    title: str
    body: str
    channels: tuple[NotificationChannel, ...] = ()


CronAction = ShellAction | WakeupAction | ActorMessageAction | ConversationCallbackAction | ReminderAction


def cron_action_kind(action: CronAction) -> ActionKind:
    if isinstance(action, ShellAction):
        return "shell"
    if isinstance(action, WakeupAction):
        return "wakeup"
    if isinstance(action, ActorMessageAction):
        return "actor_message"
    if isinstance(action, ConversationCallbackAction):
        return "conversation_callback"
    if isinstance(action, ReminderAction):
        return "reminder"
    raise TypeError(f"unsupported cron action: {type(action)!r}")


class CronJob(msgspec.Struct, frozen=True):
    id: str
    owner: str
    name: str
    schedule: CronSchedule
    action: CronAction
    status: CronJobStatus = "active"
    next_run_at: str | None = None
    last_run_at: str | None = None
    once: bool = False
    created_at: str = ""
    updated_at: str = ""


_CRON_JOB_ENCODER = msgspec.json.Encoder()


def lifecycle_once_for_schedule(schedule: CronSchedule) -> bool:
    """Return the single backend lifecycle rule for a cron schedule."""
    return schedule.kind == "at"


def encode_cron_job(job: CronJob) -> bytes:
    return _CRON_JOB_ENCODER.encode(msgspec.to_builtins(job))


def decode_cron_job(payload: bytes) -> CronJob:
    job = msgspec.json.decode(payload, type=CronJob)
    return CronJob(
        id=job.id,
        owner=job.owner,
        name=job.name,
        schedule=job.schedule,
        action=job.action,
        status=job.status,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        once=lifecycle_once_for_schedule(job.schedule),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def new_cron_job_id() -> str:
    return f"cj-{uuid.uuid4().hex[:12]}"


def cron_job_snapshot(job: CronJob) -> dict[str, object]:
    normalized = CronJob(
        id=job.id,
        owner=job.owner,
        name=job.name,
        schedule=job.schedule,
        action=job.action,
        status=job.status,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        once=lifecycle_once_for_schedule(job.schedule),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
    payload = msgspec.to_builtins(normalized)
    if isinstance(payload, dict):
        action = payload.get("action")
        if isinstance(action, dict):
            action.pop("type", None)
        return payload
    raise RuntimeError("unexpected cron job snapshot payload")
