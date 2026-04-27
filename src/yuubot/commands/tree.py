"""Tree-based command matching."""

from typing import Any, Awaitable, Callable, Literal

import attrs

from yuubot.auth import is_master_user
from yuubot.core.types import InboundMessage


CommandScope = Literal["group", "master"]


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
    scope: CommandScope = "group"
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

    def is_accessible_to(self, message: InboundMessage, master_id: int) -> bool:
        return self.scope == "group" or is_master_user(message.sender.user_id, master_id)

    def is_visible_to(self, message: InboundMessage, master_id: int) -> bool:
        """Whether this command should be exposed in help output."""
        if not self.is_accessible_to(message, master_id):
            return False
        if self.executor is not None:
            return True
        return any(sub.is_visible_to(message, master_id) for sub in self.subs)

    def find(self, route: list[str]) -> Command | None:
        """Walk the tree by route segments, return the target node or None."""
        if not route:
            return self
        target = route[0]
        for sub in self.subs:
            if sub.prefix == target:
                return sub.find(route[1:])
        return None

    def help(self, message: InboundMessage | None = None, master_id: int = 0) -> str:
        """Show this command's details + one-level sub-command summaries."""
        lines: list[str] = []
        if self.prefix:
            lines.append(f"[{self.prefix}] {self.help_text}" if self.help_text else f"[{self.prefix}]")
            lines.append(f"  范围: {'Master' if self.scope == 'master' else 'Group'}")
        visible_subs = (
            self.subs
            if message is None
            else [sub for sub in self.subs if sub.is_visible_to(message, master_id)]
        )
        if visible_subs:
            if lines:
                lines.append("")
            lines.append("子命令:")
            for sub in visible_subs:
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
