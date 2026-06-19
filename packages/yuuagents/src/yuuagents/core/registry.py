from __future__ import annotations

from typing import Awaitable, Callable, Generic, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class Registry(dict[str, T], Generic[T]):
    """Named mapping with batch-call helpers."""

    def select_intersect(self, keys: Iterable[str]) -> "Registry[T]":
        return Registry({k: self[k] for k in keys if k in self})

    def select(self, keys: Iterable[str]) -> "Registry[T]":
        return Registry({k: self[k] for k in keys})

    def broadcast(self, fn: Callable[[str, T], R]) -> "Registry[R]":
        return Registry({k: fn(k, v) for k, v in self.items()})

    async def abroadcast(self, fn: Callable[[str, T], Awaitable[R]]) -> "Registry[R]":
        return Registry({k: await fn(k, v) for k, v in self.items()})
