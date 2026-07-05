"""HTTP facade assembly for admin and public surfaces."""

from collections.abc import Callable

from starlette.types import ASGIApp

from ..app import Yuubot
from ..app.deployment import DeploymentConfig
from .auth import SessionStore
from .boundary import create_boundary_app
from .public_api import create_public_app
from .routes.admin import create_admin_app


def create_asgi_app(
    app: Yuubot,
    *,
    deployment: DeploymentConfig | None = None,
    on_shutdown: Callable[[], None] | None = None,
    sessions: SessionStore | None = None,
) -> ASGIApp:
    resolved_deployment = deployment or DeploymentConfig()
    session_store = sessions or SessionStore()
    admin_app = create_admin_app(app, resolved_deployment, session_store, on_shutdown=on_shutdown)
    public_app = create_public_app(app)
    return create_boundary_app(resolved_deployment, admin_app, public_app, sessions=session_store)
