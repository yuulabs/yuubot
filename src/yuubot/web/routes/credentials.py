"""Credential admin routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import Response

from ...app import Yuubot
from ..responses import error_response, json_response


def register_credential_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/credentials")
    async def api_credentials() -> Response:
        return json_response({"items": await app.credential_snapshots()})

    @api.delete("/api/credentials/{credential_id}")
    async def api_delete_credential(credential_id: str) -> Response:
        if not await app.delete_credential(credential_id):
            return error_response(404, "not_found", "credential not found")
        return json_response({"id": credential_id, "deleted": True})
