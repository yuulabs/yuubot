"""Tool configuration assembly for Agent definitions.

Pure functions that build per-agent tool config dicts
from ActorBinding and ActorFacadeBinding.
"""

from __future__ import annotations

from collections.abc import Iterable

import msgspec
from yuuagents import PythonImport, PythonKernelConfig
from yuuagents.python.runtime import PythonRuntime

from yuubot.core.facade import ActorFacadeBinding, facade_module_name
from yuubot.core.tools import ToolRegistry
from yuubot.core.validation import ConfigurationError
from yuubot.resources.records import ToolConfig

from ._constants import FACADE_EXPAND_FUNCTIONS, FACADE_IMPORTS, PYTHON_PROVIDER_KEY

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
) -> dict[str, dict[str, object]]:
    result = _tool_definition_configs(configs)
    if facade is not None:
        result[PYTHON_PROVIDER_KEY] = _python_agent_tool_config(
            result.get(PYTHON_PROVIDER_KEY),
            facade,
            workspace_path=workspace_path,
        )
    return result


# ── Python agent tool config ─────────────────────────────────────


def _python_agent_tool_config(
    existing: dict[str, object] | None,
    facade: ActorFacadeBinding,
    *,
    workspace_path: str | None = None,
) -> dict[str, object]:
    return msgspec.to_builtins(_python_tool_runtime(facade, workspace_path=workspace_path))


def _python_tool_runtime(
    facade: ActorFacadeBinding,
    *,
    workspace_path: str | None = None,
) -> PythonRuntime:
    return PythonRuntime(
        config=PythonKernelConfig(
            cwd=workspace_path,
            sys_path=tuple(facade.sys_path),
            startup_code=facade.startup_code,
        ),
        imports=_facade_imports(facade),
        state=_python_session_state(facade),
        expand_functions=_facade_expand_functions(facade),
    )


# ── Facade-derived config fragments ──────────────────────────────


def _facade_imports(facade: ActorFacadeBinding) -> tuple[PythonImport, ...]:
    modules = {
        facade_module_name(capability)
        for capability in facade.capabilities
    }
    return (
        *FACADE_IMPORTS,
        *(PythonImport(module=module) for module in sorted(modules) if module != "yext"),
    )


def _facade_expand_functions(facade: ActorFacadeBinding) -> tuple[str, ...]:
    modules = {
        facade_module_name(capability)
        for capability in facade.capabilities
    }
    return (
        *FACADE_EXPAND_FUNCTIONS,
        *(f"{module}.*" for module in sorted(modules) if module != "yext"),
    )


def _python_session_state(
    facade: ActorFacadeBinding,
) -> dict[str, object]:
    return {
        "actor_id": facade.actor_id,
        "agent_name": facade.agent_name,
        "session_id": facade.session_id,
        "mailbox_id": facade.mailbox_id,
    }

