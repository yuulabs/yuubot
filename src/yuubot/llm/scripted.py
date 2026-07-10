"""Scripted stream client for tests."""

import asyncio
from collections.abc import AsyncIterator

from attrs import define, field

from ..domain.messages import ConversationContext, LLMInput
from ..domain.stream import StreamEvent, TextDeltaPayload, Usage
from ..runtime.cache import CachePool
from ..util.stream import stream_stop_event


@define
class ScriptedStream:
    """Deterministic stream client replaying pre-scripted event steps; for tests."""

    steps: list[list[StreamEvent]]
    _index: int = field(default=0, init=False)

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
        if stop_event.is_set():
            yield stream_stop_event("interrupted", Usage(), {})
            return
        events = self.steps[min(self._index, len(self.steps) - 1)]
        self._index += 1
        for event in events:
            yield event

    async def close(self) -> None:
        return None


def scripted_reply(text: str) -> ScriptedStream:
    return ScriptedStream(
        [
            [
                StreamEvent("text-1", "text_delta", TextDeltaPayload(text)),
                stream_stop_event("stop", Usage(), {}),
            ]
        ]
    )
