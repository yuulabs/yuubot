from __future__ import annotations

from yuubot.runtime.pty_display import (
    PtyDisplayBuffer,
    filter_tool_output,
    render_pty_output,
)


def test_carriage_return_overwrites_line() -> None:
    assert render_pty_output("A\rB\n") == "B\n"


def test_tqdm_style_progress_updates() -> None:
    raw = "\r 10%|#         |\r 50%|#####     |\r 80%|########  |\nDone\n"
    assert render_pty_output(raw) == " 80%|########  |\nDone\n"


def test_erase_to_end_of_line() -> None:
    assert render_pty_output("garbage\x1b[2Kgood") == "good"


def test_cursor_up_overwrites_previous_line() -> None:
    raw = "line1\nline2\n\x1b[1Aoverwrite"
    assert render_pty_output(raw) == "line1\noverwrite"


def test_escape_sequence_split_across_chunks() -> None:
    buffer = PtyDisplayBuffer()
    buffer.feed("hello \x1b[")
    buffer.feed("31mred\x1b[0m")
    assert buffer.snapshot() == "hello red"


def test_filter_tool_output_strips_colors_and_bell() -> None:
    raw = "\x1b[31mred\x1b[0m\nvalue\x07"
    assert filter_tool_output(raw) == "red\nvalue"


def test_plain_multiline_output_unchanged() -> None:
    raw = "hello\nworld\n"
    assert filter_tool_output(raw) == "hello\nworld\n"


def test_snapshot_replaces_on_repeated_feed() -> None:
    buffer = PtyDisplayBuffer()
    buffer.feed("\r10%")
    assert buffer.snapshot() == "10%"
    buffer.feed("\r50%")
    assert buffer.snapshot() == "50%"
