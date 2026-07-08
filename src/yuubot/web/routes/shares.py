"""Share grant admin routes."""

from __future__ import annotations

import logging

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...app.deployment import DeploymentConfig
from ...runtime.shares import (
    ShareBadRequestError,
    ShareNotFoundError,
    SharePublishError,
    share_grant_snapshot,
)
from ..errors import internal_error_detail, internal_error_message, log_internal_error
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import PublishShareBody

_log = logging.getLogger(__name__)


def register_share_routes(api: FastAPI, app: Yuubot, deployment: DeploymentConfig) -> None:
    @api.post("/api/shares")
    async def api_create_share(request: Request) -> Response:
        try:
            body = await read_json(request, PublishShareBody)
            grant = await app.publish_share(
                actor_id=body.actor_id,
                source_path=body.source_path,
                expires_at=body.expires_at,
            )
        except ShareNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except ShareBadRequestError as exc:
            return bad_request(exc)
        except (SharePublishError, OSError) as exc:
            log_internal_error(_log, exc, "POST /api/shares")
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, app.development),
                internal_error_detail(exc, app.development),
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(share_grant_snapshot(grant, deployment.public_url_base), 201)

    @api.get("/api/shares")
    async def api_shares() -> Response:
        items = [
            share_grant_snapshot(grant, deployment.public_url_base)
            for grant in app.list_share_grants()
        ]
        return json_response({"items": items})

    @api.get("/api/shares/{share_id}")
    async def api_share(share_id: str) -> Response:
        try:
            grant = app.get_share_grant(share_id)
        except ShareNotFoundError:
            return error_response(404, "not_found", "share not found")
        return json_response(share_grant_snapshot(grant, deployment.public_url_base))

    @api.delete("/api/shares/{share_id}")
    async def api_revoke_share(share_id: str) -> Response:
        try:
            grant = await app.revoke_share(share_id)
        except ShareNotFoundError:
            return error_response(404, "not_found", "share not found")
        return json_response({"id": grant.id, "revoked": grant.revoked})
