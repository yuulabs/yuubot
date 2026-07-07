"""Actor admin routes."""

from __future__ import annotations

import mimetypes

import msgspec
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import Response

from ...app import Yuubot
from ...domain.messages import ActorMessage
from ...domain.records import ActorConfigError, ActorInput
from ...runtime.inbound import MailboxUnavailableError
from ..errors import internal_error_detail, internal_error_message
from ..files import actor_workspace, delete_entries, directory_snapshot, make_directory, move_entries, rename_entry, save_uploads, workspace_path
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import WorkspaceDeleteBody, WorkspaceMkdirBody, WorkspaceMoveBody, WorkspaceRenameBody


def register_actor_routes(api: FastAPI, app: Yuubot) -> None:
    @api.put("/api/actors/{actor_id}")
    async def api_put_actor(actor_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, ActorInput)
            await app.put_actor(actor_id, body)
        except ActorConfigError as exc:
            return error_response(422, exc.code, str(exc), detail=exc.detail)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/actors/{actor_id}")
    async def api_actor(actor_id: str) -> Response:
        record = app.actor_records.get(actor_id)
        if record is None:
            return error_response(404, "not_found", "actor not found")
        return json_response(msgspec.to_builtins(record))

    @api.post("/api/actors/{actor_id}/enable")
    async def api_enable_actor(actor_id: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.enable_actor(actor_id)
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/actors/{actor_id}/disable")
    async def api_disable_actor(actor_id: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.disable_actor(actor_id)
        return json_response(await app.bootstrap_snapshot())

    @api.delete("/api/actors/{actor_id}")
    async def api_delete_actor(actor_id: str) -> Response:
        removed = await app.remove_actor(actor_id)
        if not removed:
            return error_response(404, "not_found", "actor not found")
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/actors/{actor_id}/browse")
    async def api_browse_actor(actor_id: str, path: str = "") -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            target = workspace_path(workspace, path)
        except ValueError as exc:
            return bad_request(exc)
        if not target.is_dir():
            return error_response(404, "not_found", "directory not found")
        return json_response(directory_snapshot(workspace, target))

    @api.get("/api/actors/{actor_id}/files/{file_path:path}")
    async def api_actor_file(actor_id: str, file_path: str) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            target = workspace_path(workspace, file_path)
        except ValueError as exc:
            return bad_request(exc)
        if not target.is_file():
            return error_response(404, "not_found", "file not found")
        return Response(content=target.read_bytes(), media_type=mimetypes.guess_type(target)[0] or "application/octet-stream")

    @api.post("/api/actors/{actor_id}/uploads")
    async def api_upload_actor(actor_id: str, file: list[UploadFile] = File(...), path: str | None = None) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            return json_response({"files": await save_uploads(workspace, file, destination=path)})
        except ValueError as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/directories")
    async def api_create_workspace_directory(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceMkdirBody)
            return json_response(make_directory(workspace, body.path), status=201)
        except FileExistsError as exc:
            return error_response(409, "conflict", str(exc))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/rename")
    async def api_rename_workspace_entry(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceRenameBody)
            return json_response(rename_entry(workspace, body.path, body.name))
        except FileExistsError as exc:
            return error_response(409, "conflict", str(exc))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/move")
    async def api_move_workspace_entries(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceMoveBody)
            return json_response(move_entries(workspace, body.sources, body.destination))
        except FileExistsError as exc:
            return error_response(409, "conflict", str(exc))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.delete("/api/actors/{actor_id}/workspace/entries")
    async def api_delete_workspace_entries(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceDeleteBody)
            return json_response(delete_entries(workspace, body.paths))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/inbound")
    async def api_actor_inbound(actor_id: str, request: Request) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, ActorMessage)
            if not body.text:
                return error_response(400, "bad_request", "text is required")
            result = await app.deliver_actor_inbound(actor_id, body)
        except MailboxUnavailableError as exc:
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, development=app.runtime.development),
                detail=internal_error_detail(exc, development=app.runtime.development),
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(result)
