"""External integration plugin lifecycle and manifest support.

Re-exports all public symbols from the decomposed sub-modules so
that importers of the legacy :mod:`yuubot.runtime.plugin_manager`
continue to work unchanged.
"""

from __future__ import annotations

from yuubot.runtime.plugin._facade import ExternalPluginInboundMessage
from yuubot.runtime.plugin._lifecycle import (
    check_system_requirements,
    copy_plugin_source,
    install_plugin_environment,
)
from yuubot.runtime.plugin._manager import (
    ExternalPluginFactory,
    ExternalPluginFactoryLoader,
    ExternalPluginIntegration,
    ExternalPluginManager,
)
from yuubot.runtime.plugin._manifest import (
    ExternalPluginError,
    ExternalPluginFacadeSpec,
    ExternalPluginFunctionSpec,
    ExternalPluginIngressSpec,
    ExternalPluginManifest,
    ExternalPluginResult,
    ExternalPluginRoute,
    _capability_id,
    _input_struct,
    _plugin_root_from_archive,
    _try_load_manifest,
    load_external_plugin_manifest,
)
from yuubot.runtime.plugin._process import (
    ExternalPluginProcess,
    ExternalPluginStatus,
    PLUGIN_TOKEN_CONFIG_KEY,
    allocate_port,
    plugin_python,
    plugin_token,
    process_env,
    run_subprocess,
    wait_for_plugin_health,
)

__all__ = [
    "ExternalPluginError",
    "ExternalPluginFacadeSpec",
    "ExternalPluginFactory",
    "ExternalPluginFactoryLoader",
    "ExternalPluginFunctionSpec",
    "ExternalPluginIngressSpec",
    "ExternalPluginInboundMessage",
    "ExternalPluginIntegration",
    "ExternalPluginManifest",
    "ExternalPluginManager",
    "ExternalPluginProcess",
    "ExternalPluginResult",
    "ExternalPluginRoute",
    "ExternalPluginStatus",
    "PLUGIN_TOKEN_CONFIG_KEY",
    "_capability_id",
    "_input_struct",
    "_plugin_root_from_archive",
    "_try_load_manifest",
    "allocate_port",
    "check_system_requirements",
    "copy_plugin_source",
    "install_plugin_environment",
    "load_external_plugin_manifest",
    "plugin_python",
    "plugin_token",
    "process_env",
    "run_subprocess",
    "wait_for_plugin_health",
]
