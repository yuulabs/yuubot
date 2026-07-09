"""Terminal output filtering for yext facades."""

from __future__ import annotations

import re

from strip_ansi import strip_ansi

_OSC_CONTROL_RE = re.compile(r"\x1B\][^\x1B\x07]*(?:\x07|\x1B\\)")
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0B-\x0D\x0E-\x1F\x7F]")
_TAB_WIDTH = 8


class PtyDisplayBuffer:
    def __init__(self) -> None:
        self._lines = [""]
        self._row = 0
        self._col = 0
        self._pending = ""

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        data = f"{self._pending}{chunk}"
        self._pending = ""
        index = 0
        while index < len(data):
            char = data[index]
            if char == "\x1b":
                consumed = self._consume_escape(data, index)
                if consumed == 0:
                    self._pending = data[index:]
                    return
                index += consumed
                continue
            if char == "\r":
                self._col = 0
                index += 1
                continue
            if char == "\n":
                self._newline()
                index += 1
                continue
            if char == "\b":
                self._col = max(0, self._col - 1)
                index += 1
                continue
            if char == "\t":
                self._col = self._col + _TAB_WIDTH - (self._col % _TAB_WIDTH)
                index += 1
                continue
            if char < " " or char == "\x7f":
                index += 1
                continue
            self._write_char(char)
            index += 1

    def snapshot(self) -> str:
        if not self._lines:
            return ""
        end = self._row
        while end + 1 < len(self._lines) and self._lines[end + 1]:
            end += 1
        return "\n".join(self._lines[: end + 1])

    def _consume_escape(self, data: str, start: int) -> int:
        if data[start] != "\x1b":
            return 0
        if start + 1 >= len(data):
            return 0
        next_char = data[start + 1]
        if next_char == "]":
            end = self._find_osc_end(data, start + 2)
            if end is None:
                return 0
            return end - start
        if next_char == "[":
            end = self._find_csi_end(data, start + 2)
            if end is None:
                return 0
            body = data[start + 2 : end]
            final = data[end]
            self._dispatch_csi(body, final)
            return end - start + 1
        return 2

    def _find_osc_end(self, data: str, start: int) -> int | None:
        index = start
        while index < len(data):
            if data[index] == "\x07":
                return index + 1
            if data[index] == "\x1b" and index + 1 < len(data) and data[index + 1] == "\\":
                return index + 2
            index += 1
        return None

    def _find_csi_end(self, data: str, start: int) -> int | None:
        index = start
        while index < len(data):
            char = data[index]
            if char.isalpha() or char in "@`~":
                return index
            index += 1
        return None

    def _parse_params(self, body: str) -> list[int]:
        if not body:
            return []
        values: list[int] = []
        for part in body.split(";"):
            if not part or not part.isdigit():
                values.append(0)
            else:
                values.append(int(part))
        return values

    def _dispatch_csi(self, body: str, final: str) -> None:
        params = self._parse_params(body)
        if final == "A":
            step = params[0] if params else 1
            self._row = max(0, self._row - (step if step else 1))
            self._clamp_col()
            return
        if final == "B":
            step = params[0] if params else 1
            step = step if step else 1
            self._ensure_row(self._row + step)
            self._row += step
            self._clamp_col()
            return
        if final == "C":
            step = params[0] if params else 1
            self._col += step if step else 1
            return
        if final == "D":
            step = params[0] if params else 1
            self._col = max(0, self._col - (step if step else 1))
            return
        if final == "G":
            column = params[0] if params else 1
            self._col = max(0, (column if column else 1) - 1)
            return
        if final in {"H", "f"}:
            row = (params[0] if params else 1) - 1
            col = (params[1] if len(params) > 1 else 1) - 1
            self._ensure_row(max(0, row))
            self._row = max(0, row)
            self._col = max(0, col)
            return
        if final == "K":
            mode = params[0] if params else 0
            line = self._line_at(self._row)
            if mode == 0:
                self._lines[self._row] = line[: self._col]
            elif mode == 1:
                self._lines[self._row] = (" " * self._col) + line[self._col :]
            elif mode == 2:
                self._lines[self._row] = ""
                self._col = 0
            return
        if final == "J":
            mode = params[0] if params else 0
            if mode == 0:
                self._lines[self._row] = self._line_at(self._row)[: self._col]
                for row in range(self._row + 1, len(self._lines)):
                    self._lines[row] = ""
            elif mode == 2:
                self._lines = [""]
                self._row = 0
                self._col = 0

    def _newline(self) -> None:
        self._row += 1
        self._ensure_row(self._row)
        self._col = 0

    def _write_char(self, char: str) -> None:
        self._ensure_row(self._row)
        line = self._line_at(self._row)
        if self._col >= len(line):
            line = line + (" " * (self._col - len(line))) + char
        else:
            line = line[: self._col] + char + line[self._col + 1 :]
        self._lines[self._row] = line
        self._col += 1

    def _ensure_row(self, row: int) -> None:
        while len(self._lines) <= row:
            self._lines.append("")

    def _line_at(self, row: int) -> str:
        self._ensure_row(row)
        return self._lines[row]

    def _clamp_col(self) -> None:
        self._col = min(self._col, len(self._line_at(self._row)))


def filter_tool_output(raw: str) -> str:
    buffer = PtyDisplayBuffer()
    buffer.feed(raw)
    filtered = strip_ansi(_OSC_CONTROL_RE.sub("", buffer.snapshot()))
    return _C0_CONTROL_RE.sub("", filtered)
