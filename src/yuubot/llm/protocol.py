"""Provider runtime interface."""

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from ..domain.messages import ConversationContext, LLMInput, ModelCard
from ..domain.stream import StreamEvent, TextDeltaPayload
from ..runtime.cache import CachePool
from .types import AccountSnapshot, ValidationResult


class Provider(Protocol):
    async def list_presets(self) -> list[ModelCard]: ...

    async def list_remote_models(self) -> list[str]: ...

    def merge_catalog(self, presets: list[ModelCard], remote: list[str]) -> list[ModelCard]: ...

    async def get_balance(self) -> AccountSnapshot | None: ...

    async def validate(self) -> ValidationResult: ...

    async def stream(
        self,
        input: LLMInput,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent("", "text_delta", TextDeltaPayload())

    async def close(self) -> None: ...
