"""Tool configuration assembly for Agent definitions.

Pure functions that build per-agent tool config dicts
from ActorBinding and ActorFacadeBinding.
"""

from __future__ import annotations

from collections.abc import Iterable

import msgspec
from yuuagents import PythonImport, PythonKernelConfig
from yuuagents.python.runtime import PythonRuntime

from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.builtin_tools import BUILTIN_CAPABILITY_BY_ID
from yuubot.core.tools import ToolRegistry
from yuubot.core.validation import ConfigurationError
from yuubot.resources.records import ToolConfig

from ._constants import (
    FACADE_EXPAND_FUNCTIONS,
    FACADE_IMPORTS,
    PYTHON_PROVIDER_KEY,
    RESTART_KERNEL_TOOL_KEY,
)

_tool_registry: ToolRegistry | None = None


def set_assembly_tool_registry(registry: ToolRegistry) -> None:
    """Set the ToolRegistry used for tool name resolution during assembly.

    Called once at daemon startup.  If not set, ``_tool_definition_configs``
    falls back to yuuagents ``resolve_tool_type`` for backward compatibility
    in tests that don't go through the full daemon lifecycle.
    """
    global _tool_registry
    _tool_registry = registry


# ── Agent-level tool config ──────────────────────────────────────


def _tool_definition_configs(
    configs: Iterable[ToolConfig],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for item in configs:
        key = item.tool_name
        try:
            if _tool_registry is not None:
                _tool_registry.tool_class(key)
            else:
                from yuuagents.tool.primitives import resolve_tool_type
                resolve_tool_type(key)
        except (KeyError, LookupError):
            raise ConfigurationError(
                f"Unknown tool type {key!r} in agent_tools — "
                f"no registered yuuagents Tool subclass"
            )
        result[key] = dict(item.config)
    return result


def _agent_tool_configs(
    configs: Iterable[ToolConfig],
    facade: ActorFacadeBinding | None,
    *,
    workspace_path: str | None = None,
    integration_capability_ids: Iterable[str] = (),
) -> dict[str, dict[str, object]]:
    result = _tool_definition_configs(configs)
    result.update(_builtin_tool_configs(integration_capability_ids, workspace_path))
    if facade is not None:
        result[PYTHON_PROVIDER_KEY] = _python_agent_tool_config(
            result.get(PYTHON_PROVIDER_KEY),
            facade,
            workspace_path=workspace_path,
        )
        result[RESTART_KERNEL_TOOL_KEY] = {}
    return result


def _builtin_tool_configs(
    capability_ids: Iterable[str],
    workspace_path: str | None,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for capability_id in capability_ids:
        capability = BUILTIN_CAPABILITY_BY_ID.get(capability_id)
        if capability is None:
            continue
        if not workspace_path:
            raise ConfigurationError(
                f"{capability_id!r} requires capability_set.workspace_path"
            )
        result[capability.tool_name] = {"workspace_root": workspace_path}
    return result


# ── Python agent tool config ─────────────────────────────────────


def _python_agent_tool_config(
    existing: dict[str, object] | None,
    facade: ActorFacadeBinding,
    *,
    workspace_path: str | None = None,
) -> dict[str, object]:
    return msgspec.to_builtins(_python_tool_runtime(facade, workspace_path=workspace_path))


_PRELOADED_DATA_ALIASES = (
    "import matplotlib\n"
    'matplotlib.use("Agg")\n'
    "import pandas as pd\n"
    "import numpy as np\n"
    "import matplotlib.pyplot as plt\n"
)


def _python_tool_runtime(
    facade: ActorFacadeBinding,
    *,
    workspace_path: str | None = None,
) -> PythonRuntime:
    startup_code = facade.startup_code
    if startup_code and not startup_code.endswith("\n"):
        startup_code += "\n"
    startup_code += _PRELOADED_DATA_ALIASES
    return PythonRuntime(
        config=PythonKernelConfig(
            python=facade.venv_python,
            cwd=workspace_path,
            sys_path=tuple(facade.sys_path),
            startup_code=startup_code,
        ),
        imports=_facade_imports(facade),
        state=_python_session_state(facade),
        expand_functions=_facade_expand_functions(facade),
    )


# ── Facade-derived config fragments ──────────────────────────────


def _facade_imports(facade: ActorFacadeBinding) -> tuple[PythonImport, ...]:
    modules = _handwritten_external_modules(facade)
    return (
        *FACADE_IMPORTS,
        *(PythonImport(module=module) for module in sorted(modules)),
    )


def _facade_expand_functions(facade: ActorFacadeBinding) -> tuple[str, ...]:
    modules = _handwritten_external_modules(facade)
    return (
        *FACADE_EXPAND_FUNCTIONS,
        *(f"{module}.*" for module in sorted(modules)),
    )


def _handwritten_external_modules(facade: ActorFacadeBinding) -> set[str]:
    modules: set[str] = set()
    for capability in facade.capabilities:
        if capability.id.startswith("github."):
            modules.add("yext.github")
    return modules


def _python_session_state(
    facade: ActorFacadeBinding,
) -> dict[str, object]:
    return {
        "actor_id": facade.actor_id,
        "agent_name": facade.agent_name,
        "session_id": facade.session_id,
        "mailbox_id": facade.mailbox_id,
    }
