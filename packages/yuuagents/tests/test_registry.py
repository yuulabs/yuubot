"""Tests for registry operations and broadcast."""

from __future__ import annotations

import pytest

from yuuagents.core.registry import Registry


class _SyncCounter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def greet(self, msg: str) -> str:
        self.calls.append(msg)
        return f"{self.name}:{msg}"


class _AsyncCounter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def greet(self, msg: str) -> str:
        self.calls.append(msg)
        return f"{self.name}:{msg}"


# ---------------------------------------------------------------------------
# Registry.select / select_intersect
# ---------------------------------------------------------------------------


def test_registry_select_intersect() -> None:
    reg: Registry[int] = Registry({"a": 1, "b": 2, "c": 3})
    sub = reg.select_intersect(["a", "c", "z"])
    assert set(sub.keys()) == {"a", "c"}
    assert "z" not in sub


def test_registry_select_requires_all_keys() -> None:
    reg: Registry[int] = Registry({"a": 1, "b": 2})
    with pytest.raises(KeyError):
        reg.select(["a", "missing"])


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_async_all_keys() -> None:
    reg: Registry[_AsyncCounter] = Registry(
        {
            "x": _AsyncCounter("x"),
            "y": _AsyncCounter("y"),
        }
    )
    result = await reg.abroadcast(lambda k, v: v.greet("hello"))
    assert isinstance(result, Registry)
    assert set(result.keys()) == {"x", "y"}


@pytest.mark.asyncio
async def test_broadcast_async_intersect_with_dict() -> None:
    reg: Registry[_AsyncCounter] = Registry(
        {
            "x": _AsyncCounter("x"),
            "y": _AsyncCounter("y"),
            "z": _AsyncCounter("z"),
        }
    )
    msgs = {"x": "hi_x", "z": "hi_z"}
    result = await reg.select_intersect(msgs).abroadcast(lambda k, v: v.greet(msgs[k]))
    assert set(result.keys()) == {"x", "z"}
    assert "y" not in result


def test_broadcast_sync_all_keys() -> None:
    reg: Registry[_SyncCounter] = Registry(
        {
            "p": _SyncCounter("p"),
            "q": _SyncCounter("q"),
        }
    )
    result = reg.broadcast(lambda k, v: v.greet("test"))
    assert isinstance(result, Registry)
    assert set(result.keys()) == {"p", "q"}
