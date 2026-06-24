"""Tool type registry.

Mirrors the pattern from ``core/integrations/registry.py``:
``ToolRegistry`` ↔ ``IntegrationFactoryRegistry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from yuubot.core.tools.contracts import ToolFactory, ToolKindInfo, tool_kind_info

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool


@dataclass
class ToolRegistry:
    """Code registry for tool factory implementations.

    One registry per process.  Factories are registered at startup;
    the assembly layer resolves ``tool_name`` strings to yuuagents
    ``Tool`` subclasses via ``tool_class()``.
    """

    _factories: dict[str, ToolFactory] = field(default_factory=dict)

    def register(self, factory: ToolFactory) -> None:
        """Register a tool factory by its name.

        Duplicate registration is a hard error — tool names must be unique.
        """
        if factory.name in self._factories:
            raise ValueError(f"Tool factory {factory.name!r} is already registered")
        self._factories[factory.name] = factory

    def get(self, name: str) -> ToolFactory:
        """Look up a tool factory by name.

        Raises ``LookupError`` if the name is not registered.
        """
        try:
            return self._factories[name]
        except KeyError as exc:
            raise LookupError(
                f"Tool {name!r} is not registered — "
                f"available: {sorted(self._factories)!r}"
            ) from exc

    def tool_class(self, name: str) -> type[Tool[Any, Any]]:
        """Resolve a tool name to its yuuagents ``Tool`` subclass.

        Convenience method for the assembly layer; equivalent to
        ``registry.get(name).tool_class()``.
        """
        return self.get(name).tool_class()

    def tool_kinds(self) -> list[ToolKindInfo]:
        """Return static admin-facing descriptors for every registered kind.

        The admin UI renders a selection list from this.  Ordering follows
        registration order so the UI can display a stable catalog.
        """
        return [tool_kind_info(factory) for factory in self._factories.values()]


def default_tool_factories() -> ToolRegistry:
    """Create a ToolRegistry pre-populated with built-in tool types."""
    from yuubot.core.tools.impls.file_tools import (
        EditToolFactory,
        ReadToolFactory,
        WriteToolFactory,
    )
    from yuubot.core.tools.impls.bash import BashToolFactory
    from yuubot.core.tools.impls.execute_python import ExecutePythonToolFactory
    from yuubot.core.tools.impls.restart_kernel import RestartKernelToolFactory

    registry = ToolRegistry()
    registry.register(ExecutePythonToolFactory())
    registry.register(RestartKernelToolFactory())
    registry.register(BashToolFactory())
    registry.register(ReadToolFactory())
    registry.register(EditToolFactory())
    registry.register(WriteToolFactory())
    return registry
