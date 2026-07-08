"""Test doubles for providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from yuubot.domain import ConversationContext, LLMInput, ModelCard, StreamEvent, StreamStopPayload
from yuubot.llm import Provider, ScriptedProvider, scripted_reply
from yuubot.runtime.cache import CachePool


def scripted_reply_text(text: str) -> ScriptedProvider:
    return scripted_reply(text)


class InterruptibleProvider:
    async def list_presets(self) -> list[ModelCard]:
        return []

    async def list_remote_models(self) -> list[str]:
        return []

    def merge_catalog(self, presets: list[ModelCard], remote: list[str]) -> list[ModelCard]:
        del presets, remote
        return []

    async def get_balance(self):
        return None

    async def validate(self):
        from yuubot.llm.types import ValidationResult

        return ValidationResult(True)

    async def stream(
        self,
        input: LLMInput,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache
        for _ in range(100):
            if stop_event.is_set():
                yield StreamEvent("stop", "stream_stop", StreamStopPayload("interrupted"))
                return
            await asyncio.sleep(0.01)
        yield StreamEvent("stop", "stream_stop", StreamStopPayload("stop"))

    async def close(self) -> None:
        return None
