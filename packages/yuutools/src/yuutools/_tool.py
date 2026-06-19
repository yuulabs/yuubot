"""Core Tool, BoundTool, and the @tool decorator."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from types import GenericAlias
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast, overload

import attrs

from yuutools._depends import DependencyMarker
from yuutools._schema import type_to_json_schema
from yuutools._spec import ParamSpec, ToolSpec

Ctx = TypeVar("Ctx")
if TYPE_CHECKING:
    Result = TypeVar("Result", default=Any)
else:
    try:
        Result = TypeVar("Result", default=Any)
    except TypeError:
        Result = TypeVar("Result")

_ToolCallable = Callable[..., Result | Awaitable[Result]]


class _ToolDecorator(Protocol):
    @overload
    def __call__(self, fn: Callable[..., Awaitable[Result]]) -> Tool[Any, Result]: ...

    @overload
    def __call__(self, fn: Callable[..., Result]) -> Tool[Any, Result]: ...


class _DefaultAnyResult:
    @classmethod
    def __class_getitem__(cls, params: Any) -> Any:
        if not isinstance(params, tuple):
            params = (params, Any)
        return GenericAlias(cls, params)


@attrs.define(slots=True)
class BoundTool(_DefaultAnyResult, Generic[Ctx, Result]):
    """A tool bound to a specific context.  Ready to execute."""

    _tool: Tool[Ctx, Result] = attrs.field(repr=False)
    _ctx: Ctx = attrs.field(repr=False)

    async def run(self, *args: Any, **kwargs: Any) -> Result:
        """Execute the tool, resolving dependencies from the bound context."""
        resolved = self._tool.resolve_deps(self._ctx)
        merged = {**resolved, **kwargs}
        result = self._tool.fn(*args, **merged)
        if inspect.isawaitable(result):
            return await cast(Awaitable[Result], result)
        return cast(Result, result)


@attrs.define(slots=True)
class Tool(_DefaultAnyResult, Generic[Ctx, Result]):
    """An async-callable tool with introspected spec and dependency injection.

    Do not instantiate directly — use the :func:`tool` decorator.
    """

    fn: _ToolCallable[Result] = attrs.field(repr=False)
    spec: ToolSpec = attrs.field()
    _dep_params: dict[str, DependencyMarker] = attrs.field(factory=dict, repr=False)

    def bind(self, ctx: Ctx) -> BoundTool[Ctx, Result]:
        """Bind a context, returning a new :class:`BoundTool`."""
        return BoundTool(tool=self, ctx=ctx)

    def resolve_deps(self, ctx: Ctx) -> dict[str, Any]:
        """Resolve all dependency-injected parameters."""
        out: dict[str, Any] = {}
        for name, marker in self._dep_params.items():
            value = marker.resolver(ctx)
            if inspect.isawaitable(value):
                raise TypeError(
                    f"Dependency resolver for {name!r} returned an awaitable. "
                    "Use a sync resolver or wrap in asyncio.run()."
                )
            out[name] = value
        return out


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def tool(
    *,
    params: dict[str, str] | None = None,
    name: str = "",
    description: str = "",
) -> _ToolDecorator:
    """Decorator that turns an async function into a :class:`Tool`.

    Parameters
    ----------
    params:
        ``{param_name: description}`` mapping.  Only described params are
        exposed in the spec; dependency-injected params are always hidden.
    name:
        Override the tool name (default: function ``__name__``).
    description:
        Tool description for the LLM.  Falls back to the first line of the
        function's docstring if not provided.
    """

    def decorator(fn: Callable[..., Any]) -> Tool[Any, Any]:
        tool_name = name or fn.__name__
        tool_desc = description
        if not tool_desc and fn.__doc__:
            tool_desc = fn.__doc__.strip().split("\n", 1)[0]
        if not tool_desc:
            tool_desc = tool_name

        sig = inspect.signature(fn)
        hints = _get_type_hints_safe(fn)
        param_descs = params or {}

        spec_params: list[ParamSpec] = []
        dep_params: dict[str, DependencyMarker] = {}

        for pname, p in sig.parameters.items():
            default = p.default

            # dependency-injected — hide from spec
            if isinstance(default, DependencyMarker):
                dep_params[pname] = default
                continue

            annotation = hints.get(pname, str)
            type_schema = type_to_json_schema(annotation)
            desc = param_descs.get(pname, "")
            required = default is inspect.Parameter.empty

            spec_params.append(
                ParamSpec(
                    name=pname,
                    type_schema=type_schema,
                    description=desc,
                    required=required,
                )
            )

        spec = ToolSpec(
            name=tool_name,
            description=tool_desc,
            params=tuple(spec_params),
        )

        return Tool(fn=fn, spec=spec, dep_params=dep_params)

    return cast(_ToolDecorator, decorator)


def _get_type_hints_safe(fn: Callable[..., Any]) -> dict[str, Any]:
    """Best-effort type hint resolution."""
    try:
        import typing

        return typing.get_type_hints(fn)
    except Exception:
        return {}
