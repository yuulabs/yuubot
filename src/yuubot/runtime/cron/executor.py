"""Cron job execution."""

from __future__ import annotations

import logging
from ...util.time import utc_now_iso
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from attrs import define

from ..event_payloads import CronFailedPayload, CronFinishedPayload, CronStartedPayload
from ..tasks import parse_owner, register_shell_task
from ..wakeup import WakeupPayload, WakeupTarget
from .models import (
    ActorMessageAction,
    ConversationCallbackAction,
    CronJob,
    ReminderAction,
    ShellAction,
    WakeupAction,
    cron_action_kind,
)

if TYPE_CHECKING:
    from ..core import Runtime
    from .scheduler import CronJobScheduler

_log = logging.getLogger(__name__)
WorkspaceResolver = Callable[[str], Path | None]
SchedulerGetter = Callable[[], "CronJobScheduler"]

def _with_job(job: CronJob, **changes: object) -> CronJob:
    fields: dict[str, object] = {
        "id": job.id,
        "owner": job.owner,
        "name": job.name,
        "schedule": job.schedule,
        "action": job.action,
        "status": job.status,
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "once": job.once,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    fields.update(changes)
    return CronJob(**fields)  # type: ignore[arg-type]


@define
class CronExecutor:
    _runtime: Runtime
    _scheduler_getter: SchedulerGetter
    _workspace_resolver: WorkspaceResolver

    @property
    def _scheduler(self) -> CronJobScheduler:
        return self._scheduler_getter()

    async def run(self, job_id: str) -> None:
        try:
            job = await self._runtime.cron_jobs.get(job_id)
        except KeyError:
            return
        if job.status != "active":
            return

        actor_id, conversation_id = parse_owner(job.owner)
        workspace = self._workspace_resolver(actor_id)
        if isinstance(job.action, ShellAction):
            if workspace is None:
                _log.warning("cron job %s skipped: actor workspace not found", job_id)
                return
        elif workspace is None and isinstance(job.action, (ActorMessageAction, ConversationCallbackAction, WakeupAction)):
            if actor_id not in self._runtime.actors:
                _log.warning("cron job %s skipped: actor not running", job_id)
                return

        self._runtime.emit(
            CronStartedPayload(job_id, job.owner, cron_action_kind(job.action))
        )
        try:
            if isinstance(job.action, ShellAction):
                assert workspace is not None
                register_shell_task(
                    self._runtime,
                    job.action.name,
                    job.action.shell,
                    job.action.intro,
                    job.owner,
                    workspace,
                    "actor",
                )
            elif isinstance(job.action, WakeupAction):
                await self._runtime.wakeup.deliver(
                    WakeupTarget("actor_inbound", actor_id, None),
                    WakeupPayload(
                        job.action.text,
                        {
                            "cron_job_id": job_id,
                            "cron_job_name": job.name,
                            "cron_delivery": "actor_message",
                            "cron_legacy_kind": "wakeup",
                        },
                    ),
                )
            elif isinstance(job.action, ActorMessageAction):
                await self._runtime.wakeup.deliver(
                    WakeupTarget("actor_inbound", actor_id, None),
                    WakeupPayload(
                        job.action.text,
                        {
                            "cron_job_id": job_id,
                            "cron_job_name": job.name,
                            "cron_delivery": "actor_message",
                        },
                    ),
                )
            elif isinstance(job.action, ConversationCallbackAction):
                await self._runtime.wakeup.deliver(
                    WakeupTarget("conversation_callback", actor_id, conversation_id),
                    WakeupPayload(
                        job.action.text,
                        {
                            "cron_job_id": job_id,
                            "cron_job_name": job.name,
                            "cron_delivery": "conversation_callback",
                        },
                    ),
                )
            elif isinstance(job.action, ReminderAction):
                await self._runtime.notifications.deliver(
                    job_id=job_id,
                    action=job.action,
                    meta={"owner": job.owner, "name": job.name},
                )
        except Exception:
            _log.exception("cron job %s execution failed", job_id)
            self._runtime.emit(CronFailedPayload(job_id, job.owner))
            return

        last_run_at = utc_now_iso()
        if job.once or job.schedule.kind == "at":
            await self._runtime.cron_jobs.put(
                _with_job(job, status="completed", next_run_at=None, last_run_at=last_run_at, updated_at=utc_now_iso())
            )
            self._scheduler.unschedule(job_id)
        else:
            await self._runtime.cron_jobs.put(
                _with_job(
                    job,
                    next_run_at=self._scheduler.next_run_at(job_id),
                    last_run_at=last_run_at,
                    updated_at=utc_now_iso(),
                )
            )
        self._runtime.emit(
            CronFinishedPayload(job_id, job.owner, cron_action_kind(job.action))
        )
