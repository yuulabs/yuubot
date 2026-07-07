"""Admin update routes."""

from __future__ import annotations

from collections.abc import Callable

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...app.deployment import DeploymentConfig
from ...upgrade import apply_update, check_update, project_root
from ..client_ip import client_ip_from_scope, is_loopback
from ..responses import error_response, json_response


def register_update_routes(
    api: FastAPI,
    app: Yuubot,
    deployment: DeploymentConfig,
    *,
    on_shutdown: Callable[[], None] | None = None,
) -> None:
    trusted = frozenset(deployment.trusted_proxies)

    def client_is_loopback(request: Request) -> bool:
        return is_loopback(client_ip_from_scope(request.scope, trusted))

    @api.get("/api/admin/update/status")
    async def admin_update_status() -> Response:
        status = await check_update(project_root())
        return json_response(msgspec.to_builtins(status))

    @api.post("/api/admin/update/apply")
    async def admin_update_apply(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "admin requests require loopback access")
        if app.config_path is None:
            return error_response(500, "internal_error", "server config path is unavailable")
        try:
            result = apply_update(
                config_path=app.config_path,
                data_dir=app.runtime.data_dir,
                host=app.server_host,
                port=app.server_port,
                skip_web_build=app.runtime.development,
                on_shutdown=on_shutdown,
            )
        except ValueError as exc:
            return error_response(422, "upgrade_unsupported", str(exc))
        return json_response(msgspec.to_builtins(result))

