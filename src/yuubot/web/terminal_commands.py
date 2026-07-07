"""Admin terminal WebSocket command wire types."""

from __future__ import annotations

import msgspec


class TerminalOpenPayload(msgspec.Struct, frozen=True, kw_only=True):
    command: str = ""
    cwd: str = "~"
    rows: int = 24
    cols: int = 80


class TerminalOpenCommand(msgspec.Struct, frozen=True, kw_only=True, tag="terminal.open"):
    payload: TerminalOpenPayload = msgspec.field(default_factory=TerminalOpenPayload)


class TerminalInputPayload(msgspec.Struct, frozen=True, kw_only=True):
    data: str


class TerminalInputCommand(msgspec.Struct, frozen=True, kw_only=True, tag="terminal.input"):
    payload: TerminalInputPayload


class TerminalResizePayload(msgspec.Struct, frozen=True, kw_only=True):
    rows: int = 24
    cols: int = 80


class TerminalResizeCommand(msgspec.Struct, frozen=True, kw_only=True, tag="terminal.resize"):
    payload: TerminalResizePayload = msgspec.field(default_factory=TerminalResizePayload)


class TerminalClosePayload(msgspec.Struct, frozen=True, kw_only=True):
    pass


class TerminalCloseCommand(msgspec.Struct, frozen=True, kw_only=True, tag="terminal.close"):
    payload: TerminalClosePayload = msgspec.field(default_factory=TerminalClosePayload)


TerminalCommand = TerminalOpenCommand | TerminalInputCommand | TerminalResizeCommand | TerminalCloseCommand
