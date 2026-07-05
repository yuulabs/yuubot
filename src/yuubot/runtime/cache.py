"""Runtime-only LRU cache for derived data, sized by payload bytes."""

from collections import OrderedDict

from attrs import define, field

DEFAULT_MAX_BYTES = 64 * 1024 * 1024


@define
class CachePool:
    max_bytes: int = DEFAULT_MAX_BYTES
    _items: OrderedDict[str, tuple[dict[str, object], bytes]] = field(factory=OrderedDict)
    _size: int = field(default=0, init=False)

    def get(self, key: str) -> tuple[dict[str, object], bytes]:
        meta, data = self._items.pop(key)
        self._items[key] = (meta, data)
        return meta, data

    def set(self, key: str, meta: dict[str, object], data: bytes) -> None:
        old = self._items.pop(key, None)
        if old:
            self._size -= len(old[1])
        self._items[key] = (meta, data)
        self._size += len(data)
        while self._size > self.max_bytes and self._items:
            _, (_, evicted) = self._items.popitem(last=False)
            self._size -= len(evicted)

    def invalidate(self, *, prefix: str = "") -> None:
        if not prefix:
            self.clear()
            return
        for key in [key for key in self._items if key.startswith(prefix)]:
            _, data = self._items.pop(key)
            self._size -= len(data)

    def clear(self) -> None:
        self._items.clear()
        self._size = 0

    def __len__(self) -> int:
        return len(self._items)
