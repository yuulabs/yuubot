"""Actor KV store admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...runtime.kv import (
    KvBadRequestError,
    KvConflictError,
    KvPutBody,
    document_snapshot,
    normalize_key,
    parse_if_match,
)
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_kv_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/actors/{actor_id}/kv/{key:path}")
    async def api_kv_get(actor_id: str, key: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            document = await app.kv_get(actor_id, key)
        except KvBadRequestError as exc:
            return bad_request(exc)
        if document is None:
            return error_response(404, "not_found", "key not found")
        response = json_response(document_snapshot(document))
        response.headers["ETag"] = f'"{document.etag}"'
        return response

    @api.put("/api/actors/{actor_id}/kv/{key:path}")
    async def api_kv_put(actor_id: str, key: str, request: Request) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, KvPutBody)
            document = await app.kv_put(
                actor_id,
                key,
                body.value,
                if_match=parse_if_match(request.headers.get("if-match")),
            )
        except KvConflictError as exc:
            return error_response(409, "conflict", str(exc), {"reason": exc.reason})
        except KvBadRequestError as exc:
            return bad_request(exc)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        response = json_response(document_snapshot(document))
        response.headers["ETag"] = f'"{document.etag}"'
        return response

    @api.delete("/api/actors/{actor_id}/kv/{key:path}")
    async def api_kv_delete(actor_id: str, key: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            deleted = await app.kv_delete(actor_id, key)
        except KvBadRequestError as exc:
            return bad_request(exc)
        if not deleted:
            return error_response(404, "not_found", "key not found")
        return json_response({"actor_id": actor_id, "key": normalize_key(key), "deleted": True})
