"""Runtime task admin routes."""

from __future__ import annotations

from collections.abc import Callable

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...runtime.tasks import TaskNotRunningError, normalize_task_ttl, task_record_snapshot
from ..files import actor_workspace
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import SubmitTaskBody, TaskStdinBody


def register_task_routes(
    api: FastAPI,
    app: Yuubot,
    client_is_loopback: Callable[[Request], bool],
) -> None:
    @api.get("/api/tasks")
    async def api_tasks(owner: str | None = None, name_glob: str = "") -> Response:
        records = app.runtime.tasks.list(owner, name_glob)
        return json_response({"items": [task_record_snapshot(record) for record in records]})

    @api.get("/api/tasks/{task_id}")
    async def api_task(task_id: str) -> Response:
        if task_id not in app.runtime.tasks:
            return error_response(404, "not_found", "task not found")
        return json_response(task_record_snapshot(app.runtime.tasks.get(task_id), True))

    @api.post("/api/tasks")
    async def api_create_task(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "task submit requires loopback access")
        try:
            body = await read_json(request, SubmitTaskBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        try:
            ttl_s = normalize_task_ttl(body.delivery, body.ttl_s, True)
        except ValueError as exc:
            return bad_request(exc)
        actor_id = body.owner.split(":conv:", 1)[0].removeprefix("actor:")
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        snapshot = await app.submit_shell_task(
            name=body.name,
            shell=body.shell,
            intro=body.intro,
            owner=body.owner,
            workspace=workspace,
            wait_s=body.wait_s,
            delivery=body.delivery,
            ttl_s=ttl_s,
        )
        return json_response(snapshot)

    @api.post("/api/tasks/{task_id}/cancel")
    async def api_cancel_task(task_id: str) -> Response:
        if task_id not in app.runtime.tasks:
            return error_response(404, "not_found", "task not found")
        app.runtime.cancel_runtime_task(task_id)
        return json_response(task_record_snapshot(app.runtime.tasks.get(task_id), True))

    @api.post("/api/tasks/{task_id}/stdin")
    async def api_task_stdin(task_id: str, request: Request) -> Response:
        if task_id not in app.runtime.tasks:
            return error_response(404, "not_found", "task not found")
        try:
            body = await read_json(request, TaskStdinBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        try:
            snapshot = app.task_stdin_write(task_id, body.text)
        except TaskNotRunningError as exc:
            return error_response(409, "conflict", str(exc))
        return json_response(snapshot)
