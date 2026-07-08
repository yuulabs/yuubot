"""Explicit tool registry: name -> ToolSpec, plus config helpers."""

from pathlib import Path

import msgspec

from ..domain.messages import ConversationContext
from ..runtime.core import Runtime
from .base import Tool, ToolConfig, ToolSpec
from .bash import BASH_SPEC
from .edit import EDIT_SPEC
from .execute_python import EXECUTE_PYTHON_SPEC
from .restart_kernel import RESTART_KERNEL_SPEC
from .read import READ_SPEC
from .write import WRITE_SPEC

_REGISTRY: dict[str, ToolSpec] = {
    "read": READ_SPEC,
    "edit": EDIT_SPEC,
    "write": WRITE_SPEC,
    "bash": BASH_SPEC,
    "execute_python": EXECUTE_PYTHON_SPEC,
    "restart_kernel": RESTART_KERNEL_SPEC,
}


def register(name: str, spec: ToolSpec) -> None:
    _REGISTRY[name] = spec


def resolve(tool_type: str) -> ToolSpec:
    spec = _REGISTRY.get(tool_type)
    if spec is None:
        raise ValueError(f"unknown tool type: {tool_type}")
    return spec


def all_tool_configs() -> dict[str, ToolConfig]:
    return {name: ToolConfig(name) for name in _REGISTRY}


def build_tools(configs: dict[str, ToolConfig], context: ConversationContext, runtime: Runtime) -> dict[str, Tool]:
    return {name: resolve(config.type).factory(config, context, runtime) for name, config in configs.items()}


async def uninstall_tools(configs: dict[str, ToolConfig], workspace: Path) -> None:
    for config in configs.values():
        uninstall = resolve(config.type).uninstall
        if uninstall is not None:
            await uninstall(config, workspace)


def tool_specs(configs: dict[str, ToolConfig]) -> list[dict[str, object]]:
    """Produce OpenAI function-tool schemas for the given tool set."""
    specs: list[dict[str, object]] = []
    for name, config in configs.items():
        spec = resolve(config.type)
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec.description,
                    "parameters": _payload_schema(spec.payload_type),
                },
            }
        )
    return specs


def all_tool_specs() -> list[dict[str, object]]:
    return tool_specs(all_tool_configs())


def _payload_schema(payload_type: type[msgspec.Struct]) -> dict[str, object]:
    schema = msgspec.json.schema(payload_type)
    ref = schema.get("$ref")
    defs = schema.get("$defs")
    if isinstance(ref, str) and ref.startswith("#/$defs/") and isinstance(defs, dict):
        resolved = defs.get(ref.removeprefix("#/$defs/"))
        if isinstance(resolved, dict):
            return resolved
    return schema
