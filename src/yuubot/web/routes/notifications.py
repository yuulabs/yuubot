"""Push notification admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import PushSubscriptionBody


def register_notification_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/notifications/vapid-public-key")
    async def api_vapid_public_key() -> Response:
        return json_response({"public_key": app.vapid_public_key()})

    @api.post("/api/notifications/subscriptions")
    async def api_create_push_subscription(request: Request) -> Response:
        try:
            body = await read_json(request, PushSubscriptionBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        snapshot = await app.save_push_subscription(endpoint=body.endpoint, keys=body.keys)
        return json_response(snapshot, 201)

    @api.delete("/api/notifications/subscriptions/{subscription_id}")
    async def api_delete_push_subscription(subscription_id: str) -> Response:
        deleted = await app.delete_push_subscription(subscription_id)
        if not deleted:
            return error_response(404, "not_found", "subscription not found")
        return json_response({"id": subscription_id, "deleted": True})
