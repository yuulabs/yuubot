"""ExecutePython tool factory — registers the ``execute_python`` tool type.

This factory wraps the ``ExecutePythonTool`` yuuagents ``Tool`` subclass
(defined in ``core/assembly/_python_tool.py``) and registers it with
yuubot's ``ToolRegistry`` at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from yuuagents import PythonRuntime

from yuubot.core.assembly._constants import PYTHON_PROVIDER_KEY
from yuubot.core.assembly._python_tool import ExecutePythonTool

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool


class ExecutePythonToolFactory:
    """ToolFactory for the built-in Python execution tool."""

    @property
    def name(self) -> str:
        return PYTHON_PROVIDER_KEY

    @property
    def description(self) -> str:
        return (
            "Execute Python code in an ipykernel session with access to "
            "the agent's facade (yb, yext, tim modules). Supports stdout, "
            "stderr capture, and rich output display."
        )

    @property
    def config_schema(self) -> type[PythonRuntime]:
        return PythonRuntime

    def tool_class(self) -> type[Tool[Any, Any]]:
        return ExecutePythonTool
