"""Cron job and push notification helpers for admin routes."""

from collections.abc import Mapping

import msgspec

from ..runtime.cron import (
    CronAction,
    CronJobStatus,
    CronSchedule,
    PushSubscription,
    cron_job_snapshot,
    decode_cron_action,
    new_push_subscription_id,
)
from ..runtime.cron.vapid import vapid_public_key
from ..runtime.core import Runtime


async def create_cron_job(
    runtime: Runtime,
    *,
    owner: str,
    name: str,
    schedule: CronSchedule | Mapping[str, object],
    action: CronAction | Mapping[str, object],
    once: bool = False,
) -> dict[str, object]:
    parsed_schedule = schedule if isinstance(schedule, CronSchedule) else msgspec.convert(schedule, CronSchedule)
    parsed_action = action if isinstance(action, CronAction) else decode_cron_action(dict(action))
    job = await runtime.cron_jobs.build_new(
        owner=owner,
        name=name,
        schedule=parsed_schedule,
        action=parsed_action,
        once=once,
    )
    stored = await runtime.cron.register(job)
    return cron_job_snapshot(stored)


async def list_cron_jobs(
    runtime: Runtime,
    *,
    owner: str | None = None,
    status: CronJobStatus | str | None = None,
    name_glob: str = "",
) -> list[dict[str, object]]:
    parsed_status = status if status in {"active", "paused", "completed", "cancelled"} else None
    jobs = await runtime.cron_jobs.list_jobs(
        owner=owner,
        status=parsed_status,
        name_glob=name_glob,
    )
    if status is not None and parsed_status is None:
        jobs = [job for job in jobs if job.status == status]
    return [cron_job_snapshot(job) for job in jobs]


async def get_cron_job(runtime: Runtime, job_id: str) -> dict[str, object]:
    return cron_job_snapshot(await runtime.cron_jobs.get(job_id))


async def pause_cron_job(runtime: Runtime, job_id: str) -> dict[str, object]:
    return cron_job_snapshot(await runtime.cron.pause(job_id))


async def resume_cron_job(runtime: Runtime, job_id: str) -> dict[str, object]:
    return cron_job_snapshot(await runtime.cron.resume(job_id))


async def delete_cron_job(runtime: Runtime, job_id: str) -> bool:
    return await runtime.cron.delete(job_id)


async def save_push_subscription(runtime: Runtime, *, endpoint: str, keys: dict[str, str]) -> dict[str, object]:
    existing = await runtime.push_subscriptions.find_by_endpoint(endpoint)
    subscription = PushSubscription(
        id=existing.id if existing is not None else new_push_subscription_id(),
        endpoint=endpoint,
        keys=keys,
        created_at=existing.created_at if existing is not None else "",
    )
    stored = await runtime.push_subscriptions.put(subscription)
    return msgspec.to_builtins(stored)


def vapid_public_key_for(runtime: Runtime) -> str:
    return vapid_public_key(runtime.data_dir)
