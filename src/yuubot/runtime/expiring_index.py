"""Size-bounded in-memory index with minimum retention windows."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Generic, TypeVar

from attrs import define, field

T = TypeVar("T")

DEFAULT_MAX_SIZE_BYTES = 64 * 1024 * 1024


@define(frozen=True)
class ExpiringIndexMetadata:
    created_at: float
    min_retain_until: float
    expires_at: float | None
    last_touched_at: float


@define
class ExpiringIndex(Generic[T]):
    """Stores objects by key and evicts unprotected entries to satisfy a size budget."""

    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
    size_of: Callable[[T], int] = field(default=lambda _item: 0)
    now: Callable[[], float] = field(default=time.monotonic)
    _items: dict[str, T] = field(factory=dict)
    _metadata: dict[str, ExpiringIndexMetadata] = field(factory=dict)

    def put(
        self,
        key: str,
        item: T,
        *,
        min_retain_until: float | None = None,
        expires_at: float | None = None,
    ) -> None:
        now = self.now()
        self._items[key] = item
        self._metadata[key] = ExpiringIndexMetadata(
            created_at=self._metadata.get(key, ExpiringIndexMetadata(now, now, None, now)).created_at,
            min_retain_until=now if min_retain_until is None else min_retain_until,
            expires_at=expires_at,
            last_touched_at=now,
        )
        self.evict()

    def get(self, key: str) -> T:
        self.evict()
        item = self._items[key]
        self.touch(key)
        return item

    def pop(self, key: str) -> T:
        self.evict()
        self._metadata.pop(key, None)
        return self._items.pop(key)

    def discard(self, key: str) -> None:
        self._metadata.pop(key, None)
        self._items.pop(key, None)

    def touch(self, key: str) -> None:
        metadata = self._metadata[key]
        self._metadata[key] = ExpiringIndexMetadata(
            created_at=metadata.created_at,
            min_retain_until=metadata.min_retain_until,
            expires_at=metadata.expires_at,
            last_touched_at=self.now(),
        )

    def update_retention(
        self,
        key: str,
        *,
        min_retain_until: float | None = None,
        expires_at: float | None = None,
    ) -> None:
        self.evict()
        metadata = self._metadata[key]
        self._metadata[key] = ExpiringIndexMetadata(
            created_at=metadata.created_at,
            min_retain_until=metadata.min_retain_until if min_retain_until is None else min_retain_until,
            expires_at=expires_at,
            last_touched_at=self.now(),
        )
        self.evict()

    def metadata(self, key: str) -> ExpiringIndexMetadata:
        self.evict()
        return self._metadata[key]

    def values(self) -> list[T]:
        self.evict()
        return list(self._items.values())

    def keys(self) -> list[str]:
        self.evict()
        return list(self._items)

    def __contains__(self, key: str) -> bool:
        self.evict()
        return key in self._items

    def evict(self) -> None:
        now = self.now()
        for key, metadata in list(self._metadata.items()):
            if metadata.expires_at is not None and now >= metadata.expires_at:
                self.discard(key)

        candidates: list[tuple[float, float, str, int]] = []
        total_size = 0
        for key, item in self._items.items():
            metadata = self._metadata[key]
            if now < metadata.min_retain_until:
                continue
            size = max(0, self.size_of(item))
            total_size += size
            candidates.append((metadata.last_touched_at, metadata.created_at, key, size))

        if total_size <= self.max_size_bytes:
            return

        for _last_touched_at, _created_at, key, size in sorted(candidates):
            self.discard(key)
            total_size -= size
            if total_size <= self.max_size_bytes:
                return
