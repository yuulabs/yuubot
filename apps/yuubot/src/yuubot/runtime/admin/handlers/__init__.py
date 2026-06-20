"""Admin HTTP route handlers (package).

Re-exports all public symbols that :mod:`yuubot.runtime.admin.app`
and downstream tests depend on. Internal modules are prefixed with
underscore to signal package-private status.
"""

from __future__ import annotations

from yuubot.bootstrap.config import AdminConfig
from yuubot.runtime.admin.handlers._daemon import _request_daemon
from yuubot.runtime.admin.handlers._github_oauth import (
    _create_github_oauth_client,
    make_github_oauth_callback_handler,
    make_github_oauth_start_handler,
)
from yuubot.runtime.admin.handlers._meta import (
    make_admin_health_handler,
    make_integration_kinds_handler,
    make_live_capabilities_handler,
    make_reveal_integration_secret_handler,
    make_serve_spa_handler,
    make_tool_kinds_handler,
)
from yuubot.runtime.admin.handlers._plugin_admin import (
    make_install_plugin_handler,
    make_list_plugins_handler,
    make_uninstall_plugin_handler,
)
from yuubot.runtime.admin.handlers._provider_admin import (
    _create_provider_model_client,
    make_provider_models_handler,
    make_validate_provider_handler,
)
from yuubot.runtime.admin.handlers._proxy import (
    make_proxy_daemon_conversations_handler,
    make_proxy_daemon_resource_handler,
)
from yuubot.runtime.admin.handlers._types import DaemonClient, DaemonResponse

__all__ = [
    "AdminConfig",
    "DaemonClient",
    "DaemonResponse",
    "_create_provider_model_client",
    "_create_github_oauth_client",
    "_request_daemon",
    "make_admin_health_handler",
    "make_github_oauth_callback_handler",
    "make_github_oauth_start_handler",
    "make_install_plugin_handler",
    "make_integration_kinds_handler",
    "make_list_plugins_handler",
    "make_live_capabilities_handler",
    "make_provider_models_handler",
    "make_proxy_daemon_conversations_handler",
    "make_proxy_daemon_resource_handler",
    "make_reveal_integration_secret_handler",
    "make_serve_spa_handler",
    "make_tool_kinds_handler",
    "make_uninstall_plugin_handler",
    "make_validate_provider_handler",
]
