"""Tool configuration assembly for Stage and Agent definitions.

Pure functions that compose yuuagents ToolDefinition, PythonToolConfig,
and PythonKernelConfig from ActorBinding and ActorFacadeBinding.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import msgspec
from yuuagents import (
    PythonImport,
    PythonKernelConfig,
    ToolDefinition,
    ToolSpecConfig,
)
from yuuagents.tool_backends.ipykernel import PythonToolConfig

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import ActorFacadeBinding, facade_module_name
from yuubot.resources.records import ToolConfig

from ._constants import FACADE_EXPAND_FUNCTIONS, FACADE_IMPORTS, PYTHON_PROVIDER_KEY


# ── Stage-level tool backend config ──────────────────────────────


def _stage_tool_backend_config(
    yuuagents_config: YuuAgentsConfig,
    *,
    binding: ActorBinding,
    facade: ActorFacadeBinding | None,
) -> dict[str, Any]:
    tool_backends = {
        key: dict(value) for key, value in yuuagents_config.tool_backends.items()
    }
    if facade is not None:
        tool_backends[PYTHON_PROVIDER_KEY] = _python_tool_backend_config(
            tool_backends.get(PYTHON_PROVIDER_KEY),
            binding=binding,
            facade=facade,
        )
    return tool_backends


# ── Agent-level tool config ──────────────────────────────────────


def _tool_definition_configs(
    configs: Iterable[ToolConfig],
) -> dict[str, ToolDefinition]:
    return {
        item.provider_key: ToolDefinition(config=dict(item.config), spec=item.spec)
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


# ── Python kernel backend config ─────────────────────────────────


def _python_tool_backend_config(
    existing: object,
    *,
    binding: ActorBinding,
    facade: ActorFacadeBinding,
) -> dict[str, Any]:
    base = msgspec.convert(
        existing if isinstance(existing, Mapping) else {},
        type=PythonKernelConfig,
        strict=False,
    )
    return msgspec.to_builtins(
        PythonKernelConfig(
            python=base.python,
            cwd=str(binding.require_workspace_path()),
            inherit_envs=base.inherit_envs,
            env_allowlist=base.env_allowlist,
            extra_envs=base.extra_envs,
            sys_path=tuple(facade.sys_path),
            startup_code=_merged_startup_code(base.startup_code, facade.startup_code),
        )
    )


# ── Python agent tool config ─────────────────────────────────────


def _python_agent_tool_config(
    existing: ToolDefinition | None,
    facade: ActorFacadeBinding,
) -> ToolDefinition:
    tool = existing or ToolDefinition(spec=ToolSpecConfig(level="summary"))
    return ToolDefinition(
        config=msgspec.to_builtins(_python_tool_config(tool.config, facade)),
        spec=tool.spec,
    )


def _python_tool_config(
    raw: Mapping[str, Any],
    facade: ActorFacadeBinding,
) -> PythonToolConfig:
    base = msgspec.convert(raw, type=PythonToolConfig, strict=False)
    return PythonToolConfig(
        config=base.config,
        imports=_merged_imports(base.imports, _facade_imports(facade)),
        state=_python_session_state(base.state, facade),
        expand_functions=_merged_str_sequence(
            base.expand_functions,
            _facade_expand_functions(facade),
        ),
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
    state: dict[str, Any],
    facade: ActorFacadeBinding,
) -> dict[str, Any]:
    result = dict(state)
    result.setdefault("actor_id", facade.actor_id)
    result.setdefault("agent_name", facade.agent_name)
    result.setdefault("session_id", facade.session_id)
    result.setdefault("mailbox_id", facade.mailbox_id)
    return result


# ── Merge helpers ────────────────────────────────────────────────


def _merged_imports(
    existing: tuple[PythonImport, ...],
    required_imports: tuple[PythonImport, ...],
) -> tuple[PythonImport, ...]:
    imports = list(existing)
    existing_modules = {item.module for item in imports}
    for required_import in required_imports:
        if required_import.module not in existing_modules:
            imports.append(required_import)
    return tuple(imports)


def _merged_str_sequence(
    existing: tuple[str, ...] | None,
    required: tuple[str, ...],
) -> tuple[str, ...]:
    values = list(existing or ())
    for item in required:
        if item not in values:
            values.append(item)
    return tuple(values)


def _merged_startup_code(existing: str, required: str) -> str:
    if not existing:
        return required
    if required in existing:
        return existing
    return f"{existing}\n{required}"
