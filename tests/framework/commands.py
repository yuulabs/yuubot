"""Declared command specs for built-in command contract tests."""

from __future__ import annotations

from yuubot.core.models import Role

from tests.framework.spec import CommandExample, CommandSpec


COMMAND_SPECS: dict[tuple[str, ...], CommandSpec] = {
    ("bot", "grand"): CommandSpec(
        route=("bot", "grand"),
        min_role=Role.MOD,
        success_examples=(
            CommandExample(
                text="/ybot grand @20001 folk",
                actor_role=Role.MOD,
                notes="Minimal scoped grant.",
            ),
        ),
        denied_example=CommandExample(
            text="/ybot grand @20001 folk",
            actor_role=Role.FOLK,
        ),
    ),
    ("bot", "on"): CommandSpec(
        route=("bot", "on"),
        min_role=Role.MOD,
        success_examples=(
            CommandExample(text="/ybot on", actor_role=Role.MOD),
            CommandExample(text="/ybot on --free", actor_role=Role.MOD),
            CommandExample(
                text="/ybot on --auto",
                actor_role=Role.MOD,
                message_type="private",
                at_bot=False,
            ),
        ),
        denied_example=CommandExample(text="/ybot on", actor_role=Role.FOLK),
    ),
    ("bot", "off"): CommandSpec(
        route=("bot", "off"),
        min_role=Role.MOD,
        success_examples=(
            CommandExample(text="/ybot off", actor_role=Role.MOD),
            CommandExample(
                text="/ybot off",
                actor_role=Role.MOD,
                message_type="private",
                at_bot=False,
            ),
        ),
        denied_example=CommandExample(text="/ybot off", actor_role=Role.FOLK),
    ),
    ("bot", "set"): CommandSpec(
        route=("bot", "set"),
        min_role=Role.MOD,
        success_examples=(
            CommandExample(text="/ybot set /foo help", actor_role=Role.MOD),
        ),
        denied_example=CommandExample(text="/ybot set /foo help", actor_role=Role.FOLK),
    ),
    ("bot", "allow-dm"): CommandSpec(
        route=("bot", "allow-dm"),
        min_role=Role.MASTER,
        success_examples=(
            CommandExample(text="/ybot allow-dm @20001", actor_role=Role.MASTER),
        ),
        denied_example=CommandExample(text="/ybot allow-dm @20001", actor_role=Role.MOD),
    ),
    ("help",): CommandSpec(
        route=("help",),
        min_role=Role.FOLK,
        success_examples=(
            CommandExample(text="/yhelp", actor_role=Role.FOLK),
            CommandExample(text="/yhelp bot", actor_role=Role.FOLK),
        ),
        denied_example=CommandExample(text="/yhelp", actor_role=Role.DENY),
    ),
    ("llm",): CommandSpec(
        route=("llm",),
        min_role=Role.FOLK,
        success_examples=(
            CommandExample(text="/yllm hello", actor_role=Role.FOLK),
            CommandExample(text="/yllm #general hello", actor_role=Role.MASTER),
        ),
        denied_example=CommandExample(text="/yllm #general hello", actor_role=Role.FOLK),
        denied_reason="agent_role",
    ),
    ("hhsh",): CommandSpec(
        route=("hhsh",),
        min_role=Role.FOLK,
        success_examples=(
            CommandExample(text="/yhhsh yyds", actor_role=Role.FOLK),
        ),
        denied_example=CommandExample(text="/yhhsh yyds", actor_role=Role.DENY),
    ),
    ("close",): CommandSpec(
        route=("close",),
        min_role=Role.FOLK,
        success_examples=(
            CommandExample(text="/yclose", actor_role=Role.FOLK),
        ),
        denied_example=CommandExample(text="/yclose", actor_role=Role.DENY),
    ),
    ("cost",): CommandSpec(
        route=("cost",),
        min_role=Role.FOLK,
        success_examples=(
            CommandExample(text="/ycost", actor_role=Role.FOLK),
            CommandExample(text="/ycost --all", actor_role=Role.MASTER),
        ),
        denied_example=CommandExample(text="/ycost --all", actor_role=Role.FOLK),
        denied_reason="semantic_role",
    ),
    ("ping",): CommandSpec(
        route=("ping",),
        min_role=Role.FOLK,
        success_examples=(
            CommandExample(text="/yping", actor_role=Role.FOLK),
        ),
        denied_example=CommandExample(text="/yping", actor_role=Role.DENY),
    ),
    ("char", "show", "prompt"): CommandSpec(
        route=("char", "show", "prompt"),
        min_role=Role.MASTER,
        success_examples=(
            CommandExample(text="/ychar show prompt", actor_role=Role.MASTER),
        ),
        denied_example=CommandExample(text="/ychar show prompt", actor_role=Role.MOD),
    ),
    ("char", "show", "config"): CommandSpec(
        route=("char", "show", "config"),
        min_role=Role.MASTER,
        success_examples=(
            CommandExample(text="/ychar show config", actor_role=Role.MASTER),
        ),
        denied_example=CommandExample(text="/ychar show config", actor_role=Role.MOD),
    ),
    ("char", "config"): CommandSpec(
        route=("char", "config"),
        min_role=Role.MASTER,
        success_examples=(
            CommandExample(
                text="/ychar config main provider=test model=test-model",
                actor_role=Role.MASTER,
            ),
        ),
        denied_example=CommandExample(
            text="/ychar config main provider=test model=test-model",
            actor_role=Role.MOD,
        ),
    ),
    ("char", "list"): CommandSpec(
        route=("char", "list"),
        min_role=Role.MASTER,
        success_examples=(
            CommandExample(text="/ychar list", actor_role=Role.MASTER),
        ),
        denied_example=CommandExample(text="/ychar list", actor_role=Role.MOD),
    ),
}
