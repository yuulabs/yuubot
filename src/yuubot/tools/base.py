"""Tool contract shared by builtin tools and extensions."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol

import msgspec
from attrs import frozen

from ..domain.messages import ContentItem, ConversationContext

if TYPE_CHECKING:
    from ..runtime.core import Runtime


class ToolConfig(msgspec.Struct, frozen=True, kw_only=True):
    type: str
    options: dict[str, object] = msgspec.field(default_factory=dict)


class Tool(Protocol):
    payload_type: ClassVar[type[msgspec.Struct]]

    async def execute(self, payload: msgspec.Struct) -> str | list[ContentItem]: ...

    async def close(self) -> None: ...


ToolFactory = Callable[["ToolConfig", ConversationContext, "Runtime"], Tool]
ToolUninstaller = Callable[[ToolConfig, Path], Awaitable[None]]


@frozen
class ToolSpec:
    payload_type: type[msgspec.Struct]
    description: str
    factory: ToolFactory
    uninstall: ToolUninstaller | None = None
