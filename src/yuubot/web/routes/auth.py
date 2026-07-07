"""Admin auth routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app.deployment import DeploymentConfig
from ..auth import LoginBody, SessionStore
from ..request import bad_request, read_json
from ..responses import error_response, json_response


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
        if expected and body.password != expected:
            return error_response(401, "unauthorized", "invalid credentials")
        session_id, csrf_token = sessions.create(user_id="admin", display_name="Admin")
        response = json_response({"csrf_token": csrf_token})
        response.set_cookie(
            deployment.admin_auth.builtin.session_cookie_name,
            session_id,
            httponly=True,
            secure=True,
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
