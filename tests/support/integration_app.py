"""Helpers for module-scoped Yuubot integration tests."""

from __future__ import annotations

import asyncio
import contextlib

from yuubot.app import Yuubot


async def reset_cron_app_state(app: Yuubot) -> None:
    for job in await app.runtime.cron_jobs.list_jobs():
        await app.runtime.cron_jobs.delete(job.id)
        with contextlib.suppress(Exception):
            app.runtime.cron._scheduler.remove_job(job.id)  # noqa: SLF001
    app.runtime.eventbus._buffer.clear()  # noqa: SLF001
    while not app.runtime.eventbus.pending_empty():
        with contextlib.suppress(asyncio.QueueEmpty):
            app.runtime.eventbus.pull_nowait()


async def reset_actor_app_state(app: Yuubot, *, actor_ids: tuple[str, ...] = ("amy",)) -> None:
    for actor_id in actor_ids:
        with contextlib.suppress(Exception):
            await app.remove_actor(actor_id)
    app.runtime.eventbus._buffer.clear()  # noqa: SLF001
    while not app.runtime.eventbus.pending_empty():
        with contextlib.suppress(asyncio.QueueEmpty):
            app.runtime.eventbus.pull_nowait()
    app.runtime.conversations.ttl_s = 3600.0
