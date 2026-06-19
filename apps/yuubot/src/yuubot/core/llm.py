"""LLM backend contracts used by core actor execution."""

from __future__ import annotations

import msgspec

from yuubot.resources.records import LLMBackendRecord


class BoundLLM(msgspec.Struct):
    backend: LLMBackendRecord
    model: str
    stream_options: dict[str, object] = msgspec.field(default_factory=dict)
