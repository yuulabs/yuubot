from __future__ import annotations

import queue

from yuubot.web.errors import internal_error_message


def test_internal_error_message_never_empty_in_development() -> None:
    assert internal_error_message(queue.Empty(), development=True) == "Empty: Empty()"


def test_internal_error_message_uses_generic_message_outside_development() -> None:
    assert internal_error_message(queue.Empty(), development=False) == "internal server error"
