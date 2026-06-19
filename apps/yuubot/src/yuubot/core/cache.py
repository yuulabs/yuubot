"""Async dirty-bit cache primitive."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

T = TypeVar("T")

_EMPTY = object()


@dataclass
class Cached(Generic[T]):
    """Async lazy-reload with dirty-bit invalidation.

    Thread-safe: concurrent get() calls when invalid only trigger one loader.

    Usage:
        cache = Cached(loader=my_async_loader)
        value = await cache.get()   # loads on first call
        cache.invalidate()          # marks dirty; next get() reloads
    """

    loader: Callable[[], Awaitable[T]]
    _value: object = field(default=_EMPTY, init=False, repr=False)
    _valid: bool = field(default=False, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def invalidate(self) -> None:
        self._valid = False

    async def get(self) -> T:
        if self._valid:
            return cast(T, self._value)
        async with self._lock:
            if self._valid:
                return cast(T, self._value)
            result = await self.loader()
            self._value = result
            self._valid = True
            return result

    @property
    def is_valid(self) -> bool:
        return self._valid
