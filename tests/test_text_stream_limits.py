from __future__ import annotations

from yuubot.runtime.streams import TextStream


def test_text_stream_trims_to_max_bytes() -> None:
    stream = TextStream(max_bytes=16)
    stream.write("abcdefghijklmnop")
    stream.write("qrstuvwxyz")

    assert len("".join(stream.chunks).encode()) <= 16
    assert stream.tail(max_bytes=16).endswith("qrstuvwxyz")
