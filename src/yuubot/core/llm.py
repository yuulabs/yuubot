"""LLM backend contracts used by core actor execution."""

from __future__ import annotations

from typing import Protocol

import msgspec

from yuubot.resources.records import (
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
)


class BoundLLM(msgspec.Struct):
    backend: LLMBackendRecord
    model: str
    stream_options: dict[str, object] = msgspec.field(default_factory=dict)


class ChatRequest(msgspec.Struct):
    model: str
    messages: list[str]


class ChatResponse(msgspec.Struct):
    text: str


class LLMClient(Protocol):
    async def complete(self, request: ChatRequest) -> ChatResponse: ...


class ValidationResult(msgspec.Struct):
    ok: bool
    message: str = ""


class HealthReport(msgspec.Struct):
    ok: bool
    status: str = "unknown"
    message: str = ""


class LLMBackend(Protocol):
    """Adapter from yuubot resource record to a concrete LLM client."""

    model_capabilities: ModelCapabilities

    async def validate(self, backend: LLMBackendRecord) -> ValidationResult: ...
    async def health(self, backend: LLMBackendRecord) -> HealthReport: ...
    async def list_models(self, backend: LLMBackendRecord) -> ModelCatalog: ...
    def build_client(
        self, backend: LLMBackendRecord, api_key: str | None
    ) -> LLMClient: ...
