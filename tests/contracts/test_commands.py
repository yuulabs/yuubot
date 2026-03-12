"""Command contract enforcement for the real command tree."""

from __future__ import annotations

from yuubot.core.models import Role

from tests.framework.command import build_test_command_tree, iter_leaf_commands
from tests.framework.commands import COMMAND_SPECS


def test_every_leaf_command_has_a_declared_contract() -> None:
    root = build_test_command_tree()
    missing: list[str] = []
    extra = set(COMMAND_SPECS)

    for route, command in iter_leaf_commands(root):
        extra.discard(route)
        spec = COMMAND_SPECS.get(route)
        if spec is None:
            missing.append(" ".join(route))
            continue
        assert spec.min_role == command.min_role, route
        assert spec.success_examples, route
        assert spec.denied_example is not None, route

    assert not missing, f"Missing command specs: {', '.join(sorted(missing))}"
    assert not extra, f"Unknown command specs: {', '.join(' '.join(r) for r in sorted(extra))}"


def test_every_denied_example_is_actually_a_lower_role() -> None:
    root = build_test_command_tree()
    min_role_by_route = dict(iter_leaf_commands(root))

    for route, spec in COMMAND_SPECS.items():
        command = min_role_by_route[route]
        if spec.denied_reason == "command_role":
            assert spec.denied_example.actor_role < command.min_role, route


def test_success_examples_meet_minimum_role() -> None:
    root = build_test_command_tree()
    min_role_by_route = dict(iter_leaf_commands(root))

    for route, spec in COMMAND_SPECS.items():
        command = min_role_by_route[route]
        for example in spec.success_examples:
            assert example.actor_role >= command.min_role, (route, example.text)
            assert example.actor_role in (Role.FOLK, Role.MOD, Role.MASTER), route
