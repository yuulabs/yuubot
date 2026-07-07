"""Conversation admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI
from fastapi.responses import Response

from ...app import Yuubot
from ..responses import error_response, json_response


def register_conversation_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/conversations/{conversation_id}")
    async def api_conversation(conversation_id: str) -> Response:
        summary = await app.conversation_summary(conversation_id)
        cached = app.conversation_active(conversation_id)
        if summary is None and not cached:
            return error_response(404, "not_found", "conversation not found")
        payload: dict[str, object] = (
            msgspec.to_builtins(summary)
            if summary is not None
            else {"id": conversation_id, "message_count": 0, "last_seq": None}
        )
        payload["active"] = payload.get("status") == "active" if summary is not None else cached
        payload["history_url"] = f"/api/conversations/{conversation_id}/history"
        return json_response(payload)

    @api.get("/api/conversations/{conversation_id}/history")
    async def api_conversation_history(conversation_id: str) -> Response:
        items = await app.conversation_history(conversation_id)
        if not items and not app.conversation_active(conversation_id):
            return error_response(404, "not_found", "conversation not found")
        return json_response({"conversation_id": conversation_id, "items": items})

    @api.get("/api/conversations/{conversation_id}/costs")
    async def api_conversation_costs(conversation_id: str) -> Response:
        items = await app.conversation_costs(conversation_id)
        if not items and await app.conversation_summary(conversation_id) is None:
            return error_response(404, "not_found", "conversation not found")
        return json_response({"conversation_id": conversation_id, "items": items})

    @api.delete("/api/conversations/{conversation_id}")
    async def api_delete_conversation(conversation_id: str) -> Response:
        deleted = await app.delete_conversation(conversation_id)
        if not deleted:
            return error_response(404, "not_found", "conversation not found")
        return json_response({"id": conversation_id, "deleted": True})
