"""Tool primitives: Tool, ToolDefinition, ToolRegistry, ToolCallParams, ToolCallTask, ToolContext."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import Any, ClassVar, Generic, TypeVar

import pydantic
from attrs import define, field

from yuuagents.core.task import Task
from yuuagents.obs.entitylog import EntityLog




# ── ToolDefinition ───────────────────────────────────────────────

P = TypeVar("P", bound=pydantic.BaseModel)
R = TypeVar("R", bound=pydantic.BaseModel)


@define(frozen=True)
class ToolDefinition(Generic[P, R]):
    """Static description of a tool: schema, metadata, safety constraints."""

    name: str
    description: str
    input_model: type[P]
    output_model: type[R]
    tags: set[str] = field(factory=set)
    dangerous: bool = False


# ── Tool Abstract Base ───────────────────────────────────────────


class Tool(ABC, Generic[P, R]):
    """Runtime tool instance. One tool can serve many task invocations.

    Each subclass defines its own ``__init__`` with its own dependencies.
    Construction goes through ``from_startup`` — a classmethod that receives
    the runtime plus a typed config deserialized from the agent definition.
    """

    config_type: ClassVar[type]  # msgspec.Struct subclass for deserialization

    @classmethod
    def from_startup(cls, runtime: Any, config: Any) -> Tool[P, R]:
        """Create tool from runtime + deserialized config. Override per subclass."""
        raise NotImplementedError(f"{cls.__name__}.from_startup")

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition[P, R]: ...

    @abstractmethod
    def create_coro(
        self, task: ToolCallTask, context: ToolContext
    ) -> Coroutine[Any, Any, Any]: ...

    @abstractmethod
    async def cancel(self, task: ToolCallTask, reason: str) -> None: ...


# ── Tool type registry (global, name → class) ─────────────────────


_tool_types: dict[str, type[Tool[Any, Any]]] = {}


def register_tool_type(name: str, cls: type[Tool[Any, Any]]) -> None:
    """Register a tool class under its agent-definition name."""
    _tool_types[name] = cls


def resolve_tool_type(name: str) -> type[Tool[Any, Any]]:
    """Look up a tool class by name. Raises KeyError for unknown tools."""
    if name not in _tool_types:
        raise KeyError(f"Unknown tool type: {name!r}")
    return _tool_types[name]


# ── ToolRegistry ─────────────────────────────────────────────────


class ToolRegistry:
    """Registers and resolves tool definitions and instances by name."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition[Any, Any]] = {}
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(
        self,
        definition: ToolDefinition[P, R],
        tool: Tool[P, R],
    ) -> None:
        """Register a tool definition and its instance under definition.name."""
        self._definitions[definition.name] = definition
        self._tools[definition.name] = tool

    def resolve(self, name: str) -> tuple[ToolDefinition[Any, Any], Tool[Any, Any]]:
        """Look up a tool by name. Raises KeyError for unknown tools (fail fast)."""
        if name not in self._definitions:
            raise KeyError(f"Unknown tool: {name!r}")
        return self._definitions[name], self._tools[name]


# ── ToolCallParams ───────────────────────────────────────────────


@define
class ToolCallParams:
    """Parameters for a single tool call after validation."""

    tool_call_id: str
    tool_name: str
    params: pydantic.BaseModel  # validated by runtime


# ── ToolCallTask ─────────────────────────────────────────────────


@define
class ToolCallTask(Task[Any]):
    """A Task that executes a single tool invocation."""

    tool_call_params: ToolCallParams = field(kw_only=True)

    @property
    def info(self) -> dict[str, Any]:
        d = super().info()
        d["tool_name"] = self.tool_call_params.tool_name
        d["tool_call_id"] = self.tool_call_params.tool_call_id
        return d


# ── ToolContext ──────────────────────────────────────────────────


@define
class ToolContext:
    """Context injected into every tool invocation."""

    agent_id: str
    tool_call_id: str
    eventbus: Any  # EventBus — avoid circular import
    entity_log: EntityLog
    task_id: str | None = None  # backfilled by runtime after Task creation
