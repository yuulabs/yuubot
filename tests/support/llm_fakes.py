"""Test doubles for providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from yuubot.domain import ConversationContext, LLMInput, StreamEvent, StreamStopPayload
from yuubot.llm import ScriptedStream, scripted_reply
from yuubot.runtime.cache import CachePool


def scripted_reply_text(text: str) -> ScriptedStream:
    return scripted_reply(text)


class InterruptibleStream:
    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache, metadata
        for _ in range(100):
            if stop_event.is_set():
                yield StreamEvent("stop", "stream_stop", StreamStopPayload("interrupted"))
                return
            await asyncio.sleep(0.01)
        yield StreamEvent("stop", "stream_stop", StreamStopPayload("stop"))

    async def close(self) -> None:
        return None
