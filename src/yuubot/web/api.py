"""HTTP facade assembly for admin and public surfaces."""

from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from ..app import Yuubot
from ..app.deployment import DeploymentConfig
from .auth import AdminAuthMiddleware, AuthContext, SessionStore
from .boundary import create_boundary_app
from .public_api import create_public_app
from .routes.admin import create_admin_app


def create_asgi_app(
    app: Yuubot,
    deployment: DeploymentConfig | None = None,
    on_shutdown: Callable[[], None] | None = None,
    sessions: SessionStore | None = None,
) -> ASGIApp:
    resolved_deployment = deployment or DeploymentConfig()
    session_store = sessions or SessionStore()
    if resolved_deployment.surface == "public":
        return create_public_app(app, resolved_deployment)
    if resolved_deployment.surface == "local_admin":
        return create_local_admin_app(app, resolved_deployment, on_shutdown, session_store)
    if resolved_deployment.surface == "trusted_admin":
        return create_trusted_admin_app(app, resolved_deployment, on_shutdown, session_store)
    return create_local_dev_app(app, resolved_deployment, on_shutdown, session_store)


def create_local_admin_app(
    app: Yuubot,
    deployment: DeploymentConfig,
    on_shutdown: Callable[[], None] | None = None,
    sessions: SessionStore | None = None,
) -> ASGIApp:
    admin_app = create_admin_app(app, deployment, sessions or SessionStore(), on_shutdown)
    return LocalAdminTrustMiddleware(admin_app)


def create_trusted_admin_app(
    app: Yuubot,
    deployment: DeploymentConfig,
    on_shutdown: Callable[[], None] | None = None,
    sessions: SessionStore | None = None,
) -> ASGIApp:
    session_store = sessions or SessionStore()
    admin_app = create_admin_app(app, deployment, session_store, on_shutdown)
    return AdminAuthMiddleware(admin_app, deployment, session_store)


def create_local_dev_app(
    app: Yuubot,
    deployment: DeploymentConfig,
    on_shutdown: Callable[[], None] | None = None,
    sessions: SessionStore | None = None,
) -> ASGIApp:
    session_store = sessions or SessionStore()
    admin_app = create_admin_app(app, deployment, session_store, on_shutdown)
    public_app = create_public_app(app, deployment)
    trusted_admin = LocalAdminTrustMiddleware(admin_app)
    return create_boundary_app(deployment, trusted_admin, public_app, session_store, False)


class LocalAdminTrustMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            state = scope.setdefault("state", {})
            if isinstance(state, dict):
                state["auth"] = AuthContext("local-admin", auth_method="loopback_bypass")
        await self.app(scope, receive, send)
