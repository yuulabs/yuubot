"""Helpers for collecting command routes from the real command tree."""

from __future__ import annotations

from collections.abc import Iterator

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.tree import Command


async def _noop_llm_executor(remaining: str, event: dict, deps: dict) -> None:
    del remaining, event, deps
    return None


def build_test_command_tree():
    return build_command_tree(["/y", "/yuu"], llm_executor=_noop_llm_executor)


def iter_leaf_commands(root: Command) -> Iterator[tuple[tuple[str, ...], Command]]:
    """Yield every executable leaf command with its full route."""

    def _walk(node: Command, prefix: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Command]]:
        current = prefix + ((node.prefix,) if node.prefix else ())
        if node.executor is not None:
            yield current, node
        for sub in node.subs:
            yield from _walk(sub, current)

    yield from _walk(root, ())

