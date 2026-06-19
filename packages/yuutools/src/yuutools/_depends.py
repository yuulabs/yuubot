"""Dependency injection markers for yuutools."""

from __future__ import annotations

import enum
from typing import Any, Callable, TypeVar, overload

T = TypeVar("T")


class _Sentinel(enum.Enum):
    MISSING = enum.auto()


class DependencyMarker:
    """Marker that replaces a default value to signal dependency injection.

    At tool-call time the resolver function is called with the bound context
    to produce the real value.
    """

    __slots__ = ("resolver",)

    def __init__(self, resolver: Callable[[Any], Any]) -> None:
        self.resolver = resolver

    def __repr__(self) -> str:
        return f"DependencyMarker({self.resolver!r})"


# --- public API -----------------------------------------------------------


@overload
def depends(resolver: Callable[[Any], T]) -> T: ...


@overload
def depends(resolver: Callable[[Any], T], /) -> T: ...


def depends(resolver: Callable[[Any], Any]) -> Any:
    """Declare a parameter as context-injected.

    Usage::

        async def my_tool(x: int, db: Database = yt.depends(get_db)):
            ...

    The type-checker sees the return type of *resolver* so call-sites stay
    well-typed, but at runtime a :class:`DependencyMarker` is stored instead.
    """
    return DependencyMarker(resolver)
