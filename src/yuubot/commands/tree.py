"""Tree-based command matching."""

from typing import Callable, Awaitable

import attrs

from yuubot.core.models import Role


@attrs.define
class MatchResult:
    command: Command
    remaining: str
    entry: str


@attrs.define
class Command:
    prefix: str
    subs: list[Command] = attrs.field(factory=list)
    executor: Callable[..., Awaitable] | None = None
    min_role: Role = Role.FOLK
    help_text: str = ""

    def match(self, text: str) -> MatchResult | None:
        """Try to match text against this command and its subtree."""
        for sub in sorted(self.subs, key=lambda c: -len(c.prefix)):
            if text.startswith(sub.prefix):
                rest = text[len(sub.prefix):].lstrip()
                # Try deeper match
                deeper = sub.match(rest)
                if deeper is not None:
                    return deeper
                # This sub is the leaf
                if sub.executor is not None:
                    return MatchResult(command=sub, remaining=rest, entry="")
        # No sub matched — if we have an executor, we are the target
        if self.executor is not None:
            return MatchResult(command=self, remaining=text, entry="")
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
