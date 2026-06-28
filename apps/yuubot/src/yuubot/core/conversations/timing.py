"""Conversation trace timing helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import yuutrace


@dataclass(frozen=True)
class ConversationTimingSpan:
    span: yuutrace.TraceSpan

    def attrs(self, **fields: object) -> None:
        self.span.attrs(**{f"yuubot.{key}": value for key, value in fields.items()})


@contextmanager
def _conversation_timing_span(
    name: str,
    stage: str,
    *,
    conversation_id: str,
    **fields: object,
) -> Iterator[ConversationTimingSpan]:
    attrs = {
        "yuubot.stage": stage,
        "yuubot.conversation_id": conversation_id,
        **{f"yuubot.{key}": value for key, value in fields.items()},
    }
    with yuutrace.trace_span(name, attrs) as span:
        yield ConversationTimingSpan(span)

