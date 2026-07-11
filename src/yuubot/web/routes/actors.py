"""Actor admin routes."""

from __future__ import annotations

import logging

import msgspec
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ...app import Yuubot
from ...domain.messages import ActorMessage
from ...domain.records import ActorConfigError, ActorInput
from ...runtime.inbound import MailboxUnavailableError
from ..errors import internal_error_detail, internal_error_message, log_internal_error
from ..files import actor_workspace, delete_entries, directory_snapshot, make_directory, move_entries, rename_entry, save_uploads, workspace_media_type, workspace_path, workspace_zip
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import WorkspaceDeleteBody, WorkspaceDownloadBody, WorkspaceMkdirBody, WorkspaceMoveBody, WorkspaceRenameBody

_log = logging.getLogger(__name__)


def register_actor_routes(api: FastAPI, app: Yuubot) -> None:
    @api.put("/api/actors/{actor_id}")
    async def api_put_actor(actor_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, ActorInput)
            await app.put_actor(actor_id, body)
        except ActorConfigError as exc:
            return error_response(422, exc.code, str(exc), exc.detail)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        snapshot = await app.actor_snapshot(actor_id)
        if snapshot is None:
            return error_response(404, "not_found", "actor not found")
        return json_response(msgspec.to_builtins(snapshot))

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
        try:
            await app.enable_actor(actor_id)
        except ActorConfigError as exc:
            return error_response(422, exc.code, str(exc), exc.detail)
        snapshot = await app.actor_snapshot(actor_id)
        assert snapshot is not None
        return json_response(msgspec.to_builtins(snapshot))

    @api.post("/api/actors/{actor_id}/disable")
    async def api_disable_actor(actor_id: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.disable_actor(actor_id)
        snapshot = await app.actor_snapshot(actor_id)
        assert snapshot is not None
        return json_response(msgspec.to_builtins(snapshot))

    @api.delete("/api/actors/{actor_id}")
    async def api_delete_actor(actor_id: str) -> Response:
        removed = await app.remove_actor(actor_id)
        if not removed:
            return error_response(404, "not_found", "actor not found")
        return json_response({"id": actor_id, "deleted": True})

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
    async def api_actor_file(actor_id: str, file_path: str, download: bool = False) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            target = workspace_path(workspace, file_path)
        except ValueError as exc:
            return bad_request(exc)
        if not target.is_file():
            return error_response(404, "not_found", "file not found")
        disposition = "attachment" if download else "inline"
        return FileResponse(
            target,
            media_type=workspace_media_type(target),
            content_disposition_type=disposition,
            filename=target.name,
        )

    @api.head("/api/actors/{actor_id}/files/{file_path:path}")
    async def api_actor_file_metadata(actor_id: str, file_path: str) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            target = workspace_path(workspace, file_path)
        except ValueError as exc:
            return bad_request(exc)
        if not target.is_file():
            return error_response(404, "not_found", "file not found")
        stat = target.stat()
        return Response(headers={"Content-Length": str(stat.st_size), "Content-Type": workspace_media_type(target)})

    @api.post("/api/actors/{actor_id}/workspace/download")
    async def api_download_workspace_entries(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceDownloadBody)
            content = workspace_zip(workspace, body.paths)
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return Response(
            content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="workspace.zip"'},
        )

    @api.post("/api/actors/{actor_id}/uploads")
    async def api_upload_actor(actor_id: str, file: list[UploadFile] = File(...), path: str | None = None) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            return json_response({"files": await save_uploads(workspace, file, path)})
        except ValueError as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/directories")
    async def api_create_workspace_directory(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceMkdirBody)
            return json_response(make_directory(workspace, body.path), 201)
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
            log_internal_error(_log, exc, f"POST /api/actors/{actor_id}/inbound")
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, app.runtime.development),
                internal_error_detail(exc, app.runtime.development),
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except Exception as exc:
            log_internal_error(_log, exc, f"POST /api/actors/{actor_id}/inbound")
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, app.runtime.development),
                internal_error_detail(exc, app.runtime.development),
            )
        return json_response(result)
