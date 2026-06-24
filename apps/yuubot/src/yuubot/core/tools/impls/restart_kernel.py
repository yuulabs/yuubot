"""restart_kernel tool — closes the live execute_python kernel session lazily.

W1 wiring: ``RestartKernelTool`` does NOT receive the ``ExecutePythonTool``
instance directly. At call time it resolves the ``execute_python`` tool by name
from the yuuagents runtime registry and calls its ``restart_session()``.

Lazy restart (Phase 2 / the ``agent-python-env`` plan): closing the session
handle + nulling it; the next ``execute_python`` call re-spawns a fresh kernel
in the same workspace ``.venv`` (the config, including ``config.python``, is
unchanged). This keeps yuuagents' ``PythonSession`` untouched — the restart is
implemented at the yuubot ``ExecutePythonTool`` layer.
"""

from __future__ import annotations

from typing import Any

import msgspec
import pydantic
from yuuagents.tool.primitives import (
    Tool,
    ToolCallTask,
    ToolContext,
    ToolDefinition,
)


class RestartKernelParams(pydantic.BaseModel):
    """The restart_kernel tool takes no parameters."""


class RestartKernelConfig(msgspec.Struct):
    """restart_kernel needs no runtime configuration — it discovers the
    ``execute_python`` tool by name at call time.

    Accepts ``{}`` from the assembly ``_agent_tool_configs`` injection
    (``RESTART_KERNEL_TOOL_KEY: {}``).
    """


class RestartKernelTool(Tool[RestartKernelParams, str]):
    config_type = RestartKernelConfig

    _CONFIRMATION = (
        "Python kernel session closed. The next execute_python call will "
        "start a fresh kernel in the workspace .venv (config unchanged)."
    )

    def __init__(self, runtime: Any, *, config: RestartKernelConfig) -> None:
        self.runtime = runtime
        self.config = config

    @classmethod
    def from_startup(
        cls,
        runtime: Any,
        config: RestartKernelConfig,
    ) -> RestartKernelTool:
        return cls(runtime, config=config)

    @property
    def definition(self) -> ToolDefinition[RestartKernelParams, str]:
        return ToolDefinition(
            name="restart_kernel",
            description=(
                "Close the current Python kernel session so the next "
                "execute_python re-starts it in the workspace .venv. "
                "Call after `uv add`/`uv remove` to pick up new packages."
            ),
            input_model=RestartKernelParams,
        )

    async def create_coro(
        self, task: ToolCallTask, context: ToolContext
    ) -> str:
        # W1: discover execute_python by name at call time from the runtime
        # registry. The runtime is stage.runtime; its registry holds the live
        # ExecutePythonTool instance the agent loop dispatches through.
        registry = self.runtime.registry
        _definition, tool = registry.resolve("execute_python")
        from yuubot.core.assembly._python_tool import ExecutePythonTool

        if not isinstance(tool, ExecutePythonTool):
            raise RuntimeError(
                f"restart_kernel expected the 'execute_python' tool to be an "
                f"ExecutePythonTool, got {type(tool).__name__}"
            )
        await tool.restart_session()
        return self._CONFIRMATION

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        # restart_kernel is a single immediate action; nothing to cancel.
        return None


class RestartKernelToolFactory:
    """ToolFactory registering the ``restart_kernel`` builtin tool type."""

    @property
    def name(self) -> str:
        return "restart_kernel"

    @property
    def description(self) -> str:
        return (
            "Close the current execute_python ipykernel session so the next "
            "execute_python call re-starts it in the workspace .venv. Use "
            "after `uv add`/`uv remove` to pick up new packages."
        )

    @property
    def config_schema(self) -> type[RestartKernelConfig]:
        return RestartKernelConfig

    def tool_class(self) -> type[Tool[Any, Any]]:
        return RestartKernelTool
