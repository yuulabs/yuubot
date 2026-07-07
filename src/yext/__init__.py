"""Integration facades used from execute_python."""

from __future__ import annotations

from importlib import import_module

_FACADE_SUBMODULES = ("opencode", "codex", "web", "github")


def __getattr__(name: str) -> object:
    if name not in _FACADE_SUBMODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(f"{__name__}.{name}")
