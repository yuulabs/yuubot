"""LLM backend contracts used by core actor execution."""

from __future__ import annotations

import msgspec

from yuubot.core.validation import GenerationParams
from yuubot.resources.records import LLMBackendRecord


class BoundLLM(msgspec.Struct):
    backend: LLMBackendRecord
    model: str
    generation_params: GenerationParams = msgspec.field(default_factory=GenerationParams)
