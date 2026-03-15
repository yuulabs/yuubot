"""Tree-based command matching."""

from typing import Any, Awaitable, Callable

import attrs

from yuubot.core.types import InboundMessage
from yuubot.core.models import Role


@attrs.define
class MatchResult:
    command: Command
    command_path: tuple[str, ...]
    remaining: str
    entry: str


@attrs.define(frozen=True)
class CommandRequest:
    remaining: str
    message: InboundMessage
    deps: dict[str, Any]
    command_path: tuple[str, ...]
    entry: str = ""


@attrs.define
class Command:
    prefix: str
    subs: list[Command] = attrs.field(factory=list)
    executor: Callable[[CommandRequest], Awaitable[str | None]] | None = None
    min_role: Role = Role.FOLK
    help_text: str = ""
    interactive: bool = False  # True → queued in per-ctx worker (e.g. LLM), False → inline

    def match(self, text: str, path: tuple[str, ...] = ()) -> MatchResult | None:
        """Try to match text against this command and its subtree."""
        for sub in sorted(self.subs, key=lambda c: -len(c.prefix)):
            if text.startswith(sub.prefix):
                rest = text[len(sub.prefix):].lstrip()
                sub_path = (*path, sub.prefix)
                # Try deeper match
                deeper = sub.match(rest, sub_path)
                if deeper is not None:
                    return deeper
                # This sub is the leaf
                if sub.executor is not None:
                    return MatchResult(command=sub, command_path=sub_path, remaining=rest, entry="")
        # No sub matched — if we have an executor, we are the target
        if self.executor is not None:
            return MatchResult(command=self, command_path=path, remaining=text, entry="")
        return None

    def check_permission(self, role: Role) -> bool:
        return role >= self.min_role

    def find(self, route: list[str]) -> Command | None:
        """Walk the tree by route segments, return the target node or None."""
        if not route:
            return self
        target = route[0]
        for sub in self.subs:
            if sub.prefix == target:
                return sub.find(route[1:])
        return None

    def help(self) -> str:
        """Show this command's details + one-level sub-command summaries."""
        lines: list[str] = []
        if self.prefix:
            lines.append(f"[{self.prefix}] {self.help_text}" if self.help_text else f"[{self.prefix}]")
            lines.append(f"  权限: {self.min_role.name}")
        if self.subs:
            if lines:
                lines.append("")
            lines.append("子命令:")
            for sub in self.subs:
                desc = sub.help_text or sub.prefix
                lines.append(f"  {sub.prefix} — {desc}")
        if not lines:
            lines.append("(无帮助信息)")
        return "\n".join(lines)


@attrs.define
class RootCommand(Command):
    """Root command with entry prefix detection."""
    entries: list[str] = attrs.field(factory=list)

    def match_message(self, text: str) -> MatchResult | None:
        """Match a raw message text, stripping entry prefix first."""
        text = text.strip()
        for entry in sorted(self.entries, key=lambda e: -len(e)):
            if text.startswith(entry):
                rest = text[len(entry):]
                result = self.match(rest)
                if result is not None:
                    result.entry = entry
                    return result
        return None
