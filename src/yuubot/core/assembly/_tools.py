"""Tool configuration assembly for Agent definitions.

Pure functions that compose yuuagents ToolDefinition and PythonRuntime
from ActorBinding and ActorFacadeBinding.
"""

from __future__ import annotations

from collections.abc import Iterable

import msgspec
from yuuagents import PythonImport, PythonKernelConfig, ToolDefinition
from yuuagents.python.runtime import PythonRuntime

from yuubot.core.facade import ActorFacadeBinding, facade_module_name
from yuubot.resources.records import ToolConfig

from ._constants import FACADE_EXPAND_FUNCTIONS, FACADE_IMPORTS, PYTHON_PROVIDER_KEY


# ── Agent-level tool config ──────────────────────────────────────


def _tool_definition_configs(
    configs: Iterable[ToolConfig],
) -> dict[str, ToolDefinition]:
    return {
        item.provider_key: ToolDefinition(config=dict(item.config))
        for item in configs
    }


def _agent_tool_configs(
    configs: Iterable[ToolConfig],
    facade: ActorFacadeBinding | None,
) -> dict[str, ToolDefinition]:
    result = _tool_definition_configs(configs)
    if facade is not None:
        result[PYTHON_PROVIDER_KEY] = _python_agent_tool_config(
            result.get(PYTHON_PROVIDER_KEY),
            facade,
        )
    return result


# ── Python agent tool config ─────────────────────────────────────


def _python_agent_tool_config(
    existing: ToolDefinition | None,
    facade: ActorFacadeBinding,
) -> ToolDefinition:
    return ToolDefinition(
        config=msgspec.to_builtins(_python_tool_runtime(facade)),
    )


def _python_tool_runtime(
    facade: ActorFacadeBinding,
) -> PythonRuntime:
    return PythonRuntime(
        config=PythonKernelConfig(
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

