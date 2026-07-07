"""Runtime-only LRU cache for derived data, using TTLCache."""

import sys
from typing import Any

from attrs import define, field
from cachetools import TTLCache

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_TTL_S = 3600.0


def _get_item_size(item: object) -> int:
    get_size = getattr(item, "get_cache_size", None)
    if get_size is not None and callable(get_size):
        return int(get_size())
    if isinstance(item, tuple) and len(item) == 2:
        return _get_item_size(item[0]) + _get_item_size(item[1])
    return sys.getsizeof(item)


@define
class CachePool:
    max_bytes: int = DEFAULT_MAX_BYTES
    ttl: float = DEFAULT_TTL_S
    _cache: TTLCache[str, tuple[dict[str, Any], Any]] = field(init=False)

    def __attrs_post_init__(self) -> None:
        self._cache = TTLCache(
            maxsize=self.max_bytes,
            ttl=self.ttl,
            getsizeof=_get_item_size,
        )

    def get(self, key: str) -> tuple[dict[str, Any], Any]:
        return self._cache[key]

    def set(self, key: str, meta: dict[str, Any], data: Any) -> None:
        self._cache[key] = (meta, data)

    def invalidate(self, *, prefix: str = "") -> None:
        if not prefix:
            self.clear()
            return
        for key in list(self._cache):
            if key.startswith(prefix):
                self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
