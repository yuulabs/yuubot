"""Whitelist CLI guard for execute_skill_cli."""


from pathlib import PurePosixPath

from yuuagents.context import CliGuard

# im list targets that are forbidden in bot mode (social graph leaks)
_IM_LIST_BLOCKED = {"friends", "groups"}


def make_whitelist_guard(allowed_skills: set[str]) -> CliGuard:
    """Return a guard that only allows ``cat …/SKILL.md`` and ``ybot <skill> …``.

    Additionally blocks ``ybot im list friends`` and ``ybot im list groups``
    to prevent social graph leaks.
    """

    def _guard(argv: list[str]) -> None:
        if not argv:
            raise ValueError("empty command")

        prog = PurePosixPath(argv[0]).name

        if prog == "cat" and len(argv) == 2 and argv[1].endswith("SKILL.md"):
            return

        if prog == "ybot" and len(argv) >= 2 and argv[1] in allowed_skills:
            # Block: ybot im list friends / ybot im list groups
            if (
                argv[1] == "im"
                and len(argv) >= 4
                and argv[2] == "list"
                and argv[3] in _IM_LIST_BLOCKED
            ):
                raise ValueError(
                    f"command not allowed: im list {argv[3]} (隐私保护: 禁止查询好友/群列表)"
                )
            return

        raise ValueError(f"command not allowed: {' '.join(argv)}")

    return _guard
