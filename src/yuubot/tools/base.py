"""Tool contract shared by builtin tools and extensions."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol

import msgspec
from attrs import define, field, frozen

from ..domain.messages import ContentItem, ConversationContext

if TYPE_CHECKING:
    from ..runtime.core import Runtime


class ToolConfig(msgspec.Struct, frozen=True, kw_only=True):
    type: str
    options: dict[str, object] = msgspec.field(default_factory=dict)


class Tool(Protocol):
    payload_type: ClassVar[type[msgspec.Struct]]

    async def prepare(self) -> None: ...

    async def execute(self, payload: msgspec.Struct) -> str | list[ContentItem]: ...

    async def close(self) -> None: ...


ToolFactory = Callable[["ToolConfig", ConversationContext, "Runtime"], Tool]
ToolUninstaller = Callable[[ToolConfig, Path], Awaitable[None]]
WorkspaceExecute = Callable[..., Awaitable[str | list[ContentItem]]]


@frozen
class ToolSpec:
    payload_type: type[msgspec.Struct]
    description: str
    factory: ToolFactory
    uninstall: ToolUninstaller | None = None


def workspace_tool(
    *,
    payload_type: type[msgspec.Struct],
    description: str,
    execute: WorkspaceExecute,
    bind: Callable[[ConversationContext], dict[str, object]] | None = None,
) -> ToolSpec:
    context_bindings = bind or (lambda _context: {})
    payload_cls = payload_type

    @define
    class _WorkspaceTool:
        payload_type: ClassVar[type[msgspec.Struct]] = payload_cls
        root: Path
        bound: dict[str, object] = field(factory=dict)

        async def prepare(self) -> None:
            return None

        async def execute(self, payload: msgspec.Struct) -> str | list[ContentItem]:
            return await execute(self.root, payload, **self.bound)

        async def close(self) -> None:
            return None

    def factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> Tool:
        del config, runtime
        from .paths import workspace

        return _WorkspaceTool(root=workspace(context.workspace), bound=context_bindings(context))

    return ToolSpec(payload_type=payload_cls, description=description, factory=factory)
