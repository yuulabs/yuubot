"""Conversation admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Query, Request
from fastapi.responses import Response

from ...app import Yuubot
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_conversation_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/conversations")
    async def api_conversations() -> Response:
        return json_response(msgspec.to_builtins(await app.conversation_summaries()))

    @api.post("/api/conversations")
    async def api_create_conversation(request: Request) -> Response:
        try:
            body = await read_json(request, ConversationCreateBody)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        actor = app.actors.get(body.actor_id)
        if actor is None:
            return error_response(404, "not_found", "actor not found")
        conversation = await app.runtime.conversations.get_or_create(actor)
        await app.runtime.state.put_conversation(conversation.id, body.actor_id, "closed")
        return json_response({"conversation_id": conversation.id})

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
    async def api_conversation_history(
        conversation_id: str,
        after_seq: int | None = Query(default=None),
        before_seq: int | None = Query(default=None),
        limit: int | None = Query(default=None),
    ) -> Response:
        if after_seq is not None and after_seq < 0:
            return bad_request(ValueError("after_seq must be non-negative"))
        if before_seq is not None and before_seq < 0:
            return bad_request(ValueError("before_seq must be non-negative"))
        if after_seq is not None and before_seq is not None:
            return bad_request(ValueError("after_seq and before_seq are mutually exclusive"))
        if limit is not None and limit <= 0:
            return bad_request(ValueError("limit must be positive"))
        items, has_more = await app.conversation_history(
            conversation_id,
            after_seq,
            limit,
            before_seq,
        )
        if (
            not items
            and not app.conversation_active(conversation_id)
            and await app.conversation_summary(conversation_id) is None
        ):
            return error_response(404, "not_found", "conversation not found")
        first_seq = items[0]["seq"] if items else None
        last_seq = items[-1]["seq"] if items else None
        return json_response(
            {
                "conversation_id": conversation_id,
                "items": items,
                "has_more": has_more,
                "first_seq": first_seq,
                "last_seq": last_seq,
            }
        )

    @api.get("/api/conversations/{conversation_id}/usage")
    async def api_conversation_usage(conversation_id: str) -> Response:
        items = await app.conversation_usage(conversation_id)
        if not items and await app.conversation_summary(conversation_id) is None:
            return error_response(404, "not_found", "conversation not found")
        return json_response({"conversation_id": conversation_id, "items": items})

    @api.delete("/api/conversations/{conversation_id}")
    async def api_delete_conversation(conversation_id: str) -> Response:
        deleted = await app.delete_conversation(conversation_id)
        if not deleted:
            return error_response(404, "not_found", "conversation not found")
        return json_response({"id": conversation_id, "deleted": True})


class ConversationCreateBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    actor_id: str
