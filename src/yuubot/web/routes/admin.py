"""Admin HTTP + WebSocket route registration."""

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from ...app import Yuubot
from ...app.deployment import DeploymentConfig
from ..auth import SessionStore
from ..html import html_page
from ..responses import error_response, json_response
from ._helpers import make_client_is_loopback, react_dist_dir
from .actors import register_actor_routes
from .admin_ops import register_admin_ops_routes
from .auth import register_auth_routes
from .auth_attempts import register_auth_attempt_routes
from .bootstrap import register_bootstrap_routes
from .conversations import register_conversation_routes
from .credentials import register_credential_routes
from .cron_jobs import register_cron_routes
from .integrations import register_integration_routes
from .kv import register_kv_routes
from .mcp_servers import register_mcp_routes
from .notifications import register_notification_routes
from .providers import register_provider_routes
from .route_table import register_route_table_routes
from .shares import register_share_routes
from .skills import register_skill_routes
from .tasks import register_task_routes
from .terminal import register_terminal_routes
from .update import register_update_routes
from .websocket import register_websocket_routes


def create_admin_app(
    app: Yuubot,
    deployment: DeploymentConfig,
    sessions: SessionStore,
    on_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    api = FastAPI()
    api.state.deployment = deployment
    api.state.sessions = sessions
    client_is_loopback = make_client_is_loopback(frozenset(deployment.trusted_proxies))

    react_dist = react_dist_dir()
    if (react_dist / "assets").exists():
        api.mount("/assets", StaticFiles(directory=react_dist / "assets"), name="assets")

    @api.get("/sw.js")
    async def service_worker() -> Response:
        path = react_dist / "sw.js"
        if not path.exists():
            return error_response(404, "not_found", "service worker not found")
        return FileResponse(path, media_type="application/javascript")

    @api.get("/", response_class=HTMLResponse)
    async def html():
        index = react_dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return html_page(app)

    @api.get("/healthz")
    async def healthz() -> Response:
        return json_response({"status": "ok"})

    register_auth_routes(api, deployment, sessions)
    register_admin_ops_routes(api, app, client_is_loopback, on_shutdown)
    register_bootstrap_routes(api, app, deployment)
    register_mcp_routes(api, app, deployment)
    register_credential_routes(api, app)
    register_skill_routes(api, app)
    register_auth_attempt_routes(api, app)
    register_provider_routes(api, app)
    register_actor_routes(api, app)
    register_integration_routes(api, app)
    register_conversation_routes(api, app)
    register_route_table_routes(api, app)
    register_task_routes(api, app, client_is_loopback)
    register_cron_routes(api, app)
    register_notification_routes(api, app)
    register_share_routes(api, app, deployment)
    register_kv_routes(api, app)
    register_websocket_routes(api, app)
    register_terminal_routes(api, app)
    register_update_routes(api, app, deployment, on_shutdown)

    @api.get("/{path:path}", response_class=HTMLResponse)
    async def react_app(path: str):
        if path.startswith("api/"):
            return error_response(404, "not_found", "API endpoint not found")
        index = react_dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return html_page(app)

    return api
