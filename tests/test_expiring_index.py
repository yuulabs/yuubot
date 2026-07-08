from __future__ import annotations

import pytest

from yuubot.runtime.expiring_index import ExpiringIndex


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_expiring_index_does_not_count_or_evict_protected_items() -> None:
    clock = Clock()
    index: ExpiringIndex[str] = ExpiringIndex(1, lambda item: len(item), clock)

    index.put("protected", "too-large", min_retain_until=10)

    assert "protected" in index
    clock.advance(11)
    assert "protected" not in index


def test_expiring_index_evicts_unprotected_items_to_budget_by_lru() -> None:
    clock = Clock()
    index: ExpiringIndex[str] = ExpiringIndex(4, lambda item: len(item), clock)

    index.put("old", "aa")
    clock.advance(1)
    index.put("middle", "bb")
    clock.advance(1)
    index.put("new", "cc")

    assert "old" not in index
    assert "middle" in index
    assert "new" in index


def test_expiring_index_shared_instances_compete_for_budget() -> None:
    clock = Clock()
    shared: ExpiringIndex[str] = ExpiringIndex(3, lambda item: len(item), clock)

    shared.put("a:item", "aa")
    clock.advance(1)
    shared.put("b:item", "bb")

    assert "a:item" not in shared
    assert "b:item" in shared

    first: ExpiringIndex[str] = ExpiringIndex(3, lambda item: len(item), clock)
    second: ExpiringIndex[str] = ExpiringIndex(3, lambda item: len(item), clock)
    first.put("a:item", "aa")
    second.put("b:item", "bb")

    assert "a:item" in first
    assert "b:item" in second


def test_expiring_index_get_expires_items() -> None:
    clock = Clock()
    index: ExpiringIndex[str] = ExpiringIndex(100, lambda item: len(item), clock)
    index.put("item", "value", min_retain_until=5, expires_at=5)

    assert index.get("item") == "value"
    clock.advance(5)
    with pytest.raises(KeyError):
        index.get("item")
