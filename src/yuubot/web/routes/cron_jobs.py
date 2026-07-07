"""Cron job admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...app.cron import (
    create_cron_job,
    delete_cron_job,
    get_cron_job,
    list_cron_jobs,
    pause_cron_job,
    resume_cron_job,
)
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import CreateCronJobBody


def register_cron_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/cron-jobs")
    async def api_cron_jobs(owner: str | None = None, status: str | None = None, name_glob: str = "") -> Response:
        items = await list_cron_jobs(app.runtime, owner=owner, status=status, name_glob=name_glob)
        return json_response({"items": items})

    @api.get("/api/cron-jobs/{job_id}")
    async def api_cron_job(job_id: str) -> Response:
        try:
            return json_response(await get_cron_job(app.runtime, job_id))
        except KeyError:
            return error_response(404, "not_found", "cron job not found")

    @api.post("/api/cron-jobs")
    async def api_create_cron_job(request: Request) -> Response:
        try:
            body = await read_json(request, CreateCronJobBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        from ...runtime.cron import CronSchedule, CronScheduleError, decode_cron_action

        try:
            schedule = msgspec.convert(body.schedule, CronSchedule)
            action = decode_cron_action(body.action)
            snapshot = await create_cron_job(
                app.runtime,
                owner=body.owner,
                name=body.name,
                schedule=schedule,
                action=action,
                once=body.once,
            )
        except (msgspec.ValidationError, TypeError, ValueError, CronScheduleError) as exc:
            return bad_request(exc)
        return json_response(snapshot, status=201)

    @api.post("/api/cron-jobs/{job_id}/pause")
    async def api_pause_cron_job(job_id: str) -> Response:
        try:
            return json_response(await pause_cron_job(app.runtime, job_id))
        except KeyError:
            return error_response(404, "not_found", "cron job not found")

    @api.post("/api/cron-jobs/{job_id}/resume")
    async def api_resume_cron_job(job_id: str) -> Response:
        try:
            return json_response(await resume_cron_job(app.runtime, job_id))
        except KeyError:
            return error_response(404, "not_found", "cron job not found")

    @api.delete("/api/cron-jobs/{job_id}")
    async def api_delete_cron_job(job_id: str) -> Response:
        deleted = await delete_cron_job(app.runtime, job_id)
        if not deleted:
            return error_response(404, "not_found", "cron job not found")
        return json_response({"id": job_id, "deleted": True})
