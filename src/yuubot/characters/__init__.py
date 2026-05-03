"""RFC2 character registry for yuubot."""

from __future__ import annotations

from yuubot.prompt import AgentSpec, Character, DelegatePolicy

CHARACTER_REGISTRY: dict[str, Character] = {}


def register(character: Character) -> Character:
    CHARACTER_REGISTRY[character.name] = character
    return character


def unregister(name: str) -> None:
    CHARACTER_REGISTRY.pop(name, None)


def get_character(name: str) -> Character:
    return CHARACTER_REGISTRY[name]


# Import character modules to trigger registration
from yuubot.characters import general, mem_curator, shiori, yuu  # noqa: E402, F401

__all__ = [
    "AgentSpec",
    "CHARACTER_REGISTRY",
    "Character",
    "DelegatePolicy",
    "get_character",
    "register",
    "unregister",
]
