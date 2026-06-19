"""ToolManager — typed registry for tools."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Generic, TypeVar

import attrs

from yuutools._tool import Result, Tool, _DefaultAnyResult

Ctx = TypeVar("Ctx")


@attrs.define(slots=True, init=False)
class ToolManager(_DefaultAnyResult, Generic[Ctx, Result]):
    """A simple name-keyed registry of :class:`Tool` objects.

    Usage::

        manager = ToolManager[MyCtx]([tool_a, tool_b])
        t = manager["tool_a"]
    """

    _tools: dict[str, Tool[Ctx, Result]] = attrs.field(factory=dict, repr=False)

    def __init__(self, tools: Iterable[Tool[Ctx, Result]] | None = None) -> None:
        self._tools = {}
        for t in tools or []:
            self.register(t)

    def register(self, t: Tool[Ctx, Result]) -> None:
        """Add a tool to the registry.  Raises on duplicate names."""
        if t.spec.name in self._tools:
            raise ValueError(f"duplicate tool name: {t.spec.name!r}")
        self._tools[t.spec.name] = t

    def __getitem__(self, name: str) -> Tool[Ctx, Result]:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(
                f"no tool named {name!r}; available: {list(self._tools)}"
            ) from None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool[Ctx, Result]]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """Return JSON-schema-ready specs for all tools (for LLM function calling)."""
        import json

        return [json.loads(t.spec.to("json_schema")) for t in self._tools.values()]

    async def run(
        self,
        name: str,
        ctx: Ctx,
        arguments: Mapping[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> Result:
        """Lookup, bind, and execute a tool with keyword arguments."""
        merged = dict(arguments or {})
        merged.update(kwargs)
        return await self[name].bind(ctx).run(**merged)
