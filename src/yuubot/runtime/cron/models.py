"""Cron job data models."""

from __future__ import annotations

import uuid
from typing import Literal

import msgspec

CronJobStatus = Literal["active", "paused", "completed", "cancelled"]
ScheduleKind = Literal["cron", "at"]
ActionKind = Literal["shell", "wakeup", "actor_message", "conversation_callback", "reminder"]


class CronSchedule(msgspec.Struct, frozen=True, kw_only=True):
    kind: ScheduleKind
    timezone: str
    cron: str | None = None
    at: str | None = None


class NotificationChannel(msgspec.Struct, frozen=True, kw_only=True):
    kind: str
    config: dict[str, object] = msgspec.field(default_factory=dict)


class ShellAction(msgspec.Struct, frozen=True, kw_only=True, tag="shell"):
    kind: Literal["shell"] = "shell"
    name: str
    shell: str
    intro: str


class WakeupAction(msgspec.Struct, frozen=True, kw_only=True, tag="wakeup"):
    kind: Literal["wakeup"] = "wakeup"
    text: str
    conversation_id: str | None = None


class ActorMessageAction(msgspec.Struct, frozen=True, kw_only=True, tag="actor_message"):
    kind: Literal["actor_message"] = "actor_message"
    text: str


class ConversationCallbackAction(msgspec.Struct, frozen=True, kw_only=True, tag="conversation_callback"):
    kind: Literal["conversation_callback"] = "conversation_callback"
    text: str


class ReminderAction(msgspec.Struct, frozen=True, kw_only=True, tag="reminder"):
    kind: Literal["reminder"] = "reminder"
    title: str
    body: str
    channels: tuple[NotificationChannel, ...] = ()


CronAction = ShellAction | WakeupAction | ActorMessageAction | ConversationCallbackAction | ReminderAction


class CronJob(msgspec.Struct, frozen=True, kw_only=True):
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


def decode_cron_action(payload: dict[str, object]) -> CronAction:
    kind = payload.get("kind")
    if kind == "shell":
        return msgspec.convert(payload, ShellAction)
    if kind == "wakeup":
        return msgspec.convert(payload, WakeupAction)
    if kind == "actor_message":
        return msgspec.convert(payload, ActorMessageAction)
    if kind == "conversation_callback":
        return msgspec.convert(payload, ConversationCallbackAction)
    if kind == "reminder":
        return msgspec.convert(payload, ReminderAction)
    raise ValueError(f"unknown cron action kind: {kind!r}")


def encode_cron_job(job: CronJob) -> bytes:
    return _CRON_JOB_ENCODER.encode(msgspec.to_builtins(job))


def decode_cron_job(payload: bytes) -> CronJob:
    raw = msgspec.json.decode(payload, type=dict[str, object])
    action_raw = raw.get("action")
    if not isinstance(action_raw, dict):
        raise ValueError("cron job action must be an object")
    schedule_raw = raw.get("schedule")
    if not isinstance(schedule_raw, dict):
        raise ValueError("cron job schedule must be an object")
    schedule = msgspec.convert(schedule_raw, CronSchedule)
    return CronJob(
        id=str(raw["id"]),
        owner=str(raw["owner"]),
        name=str(raw["name"]),
        schedule=schedule,
        action=decode_cron_action(action_raw),
        status=str(raw.get("status", "active")),  # type: ignore[arg-type]
        next_run_at=raw.get("next_run_at") if isinstance(raw.get("next_run_at"), str) else None,
        last_run_at=raw.get("last_run_at") if isinstance(raw.get("last_run_at"), str) else None,
        once=lifecycle_once_for_schedule(schedule),
        created_at=str(raw.get("created_at", "")),
        updated_at=str(raw.get("updated_at", "")),
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
