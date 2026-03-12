"""Command contract specs used by test automation."""

from __future__ import annotations

from collections.abc import Callable

import attrs

from yuubot.core.models import Role


@attrs.define(frozen=True)
class CommandExample:
    """One concrete, runnable example for a command contract."""

    text: str
    message_type: str = "group"
    at_bot: bool = True
    actor_role: Role = Role.FOLK
    notes: str = ""


@attrs.define(frozen=True)
class CommandSpec:
    """Test contract for one leaf command route."""

    route: tuple[str, ...]
    min_role: Role
    success_examples: tuple[CommandExample, ...]
    denied_example: CommandExample
    denied_reason: str = "command_role"
    setup: Callable | None = None

    def route_text(self) -> str:
        return " ".join(self.route)
