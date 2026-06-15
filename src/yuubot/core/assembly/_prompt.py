"""System prompt construction helpers."""

from __future__ import annotations

from typing import Literal

from ._constants import IM_MODE_SYSTEM_GUIDANCE


def _system_prompt(
    character_prompt: str,
    mode: Literal["im", "conversation"],
) -> str:
    if mode == "conversation":
        return character_prompt
    if not character_prompt:
        return IM_MODE_SYSTEM_GUIDANCE
    return f"{character_prompt}\n\n{IM_MODE_SYSTEM_GUIDANCE}"
