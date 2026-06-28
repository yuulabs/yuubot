"""Rendering helpers for streamed tool output."""

from __future__ import annotations

import re
from collections.abc import Iterable

_ANSI_PATTERN = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]"
)


def render_tool_output_final_text(raw_chunks: str | Iterable[str]) -> str:
    raw_text = raw_chunks if isinstance(raw_chunks, str) else "".join(raw_chunks)
    clean_text = _ANSI_PATTERN.sub("", raw_text)
    rendered = _render_carriage_returns(clean_text)
    return _render_backspaces_and_strip_controls(rendered)


def _render_carriage_returns(text: str) -> str:
    lines: list[str] = []
    current: list[str] = []
    replacement: list[str] | None = None

    for char in text:
        if char == "\r":
            current = replacement if replacement is not None else current
            replacement = []
        elif char == "\n":
            current = _merged_line(current, replacement)
            replacement = None
            lines.append("".join(current))
            current = []
        elif replacement is not None:
            replacement.append(char)
        else:
            current.append(char)

    current = _final_line(current, replacement)
    if current:
        lines.append("".join(current))
    if text.endswith("\n"):
        return "\n".join(lines) + "\n"
    return "\n".join(lines)


def _merged_line(current: list[str], replacement: list[str] | None) -> list[str]:
    if replacement is None:
        return current
    return [*current, *replacement]


def _final_line(current: list[str], replacement: list[str] | None) -> list[str]:
    if replacement is None:
        return current
    if replacement:
        return replacement
    return current


def _render_backspaces_and_strip_controls(text: str) -> str:
    result: list[str] = []
    for char in text:
        if char == "\b":
            if result and result[-1] != "\n":
                result.pop()
            continue
        if _is_unsupported_control(char):
            continue
        result.append(char)
    return "".join(result)


def _is_unsupported_control(char: str) -> bool:
    return ord(char) < 32 and char not in {"\t", "\n", "\r", "\b"}
