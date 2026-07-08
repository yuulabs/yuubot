"""Admin auth routes."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app.deployment import DeploymentConfig
from ..auth import LoginBody, SessionStore
from ..request import bad_request, read_json
from ..responses import error_response, json_response

SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def register_auth_routes(api: FastAPI, deployment: DeploymentConfig, sessions: SessionStore) -> None:
    @api.post("/api/auth/login")
    async def auth_login(request: Request) -> Response:
        if deployment.admin_auth.mode != "builtin":
            return error_response(404, "not_found", "builtin auth is not enabled")
        try:
            body = await read_json(request, LoginBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        expected = deployment.admin_auth.builtin.password
        if not expected.strip():
            return error_response(500, "server_misconfigured", "builtin auth password is not configured")
        if not secrets.compare_digest(body.password, expected):
            return error_response(401, "unauthorized", "invalid credentials")
        session_id, csrf_token = sessions.create(user_id="admin", display_name="Admin")
        response = json_response({"csrf_token": csrf_token})
        response.set_cookie(
            deployment.admin_auth.builtin.session_cookie_name,
            session_id,
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            secure=urlparse(deployment.admin_url_base).scheme == "https",
            samesite="lax",
        )
        return response

    @api.post("/api/auth/logout")
    async def auth_logout(request: Request) -> Response:
        if deployment.admin_auth.mode != "builtin":
            return error_response(404, "not_found", "builtin auth is not enabled")
        cookie_name = deployment.admin_auth.builtin.session_cookie_name
        session_id = request.cookies.get(cookie_name)
        if session_id is not None:
            sessions.delete(session_id)
        response = json_response({"logged_out": True})
        response.delete_cookie(cookie_name)
        return response

    @api.get("/api/auth/session")
    async def auth_session(request: Request) -> Response:
        if deployment.admin_auth.mode != "builtin":
            return error_response(404, "not_found", "builtin auth is not enabled")
        cookie_name = deployment.admin_auth.builtin.session_cookie_name
        session_id = request.cookies.get(cookie_name)
        if session_id is None:
            return error_response(401, "unauthorized", "authentication required")
        session = sessions.get(session_id)
        if session is None:
            return error_response(401, "unauthorized", "authentication required")
        return json_response(
            {
                "user_id": session.user_id,
                "display_name": session.display_name,
                "csrf_token": session.csrf_token,
                "created_at": session.created_at,
                "last_seen_at": session.last_seen_at,
            }
        )
