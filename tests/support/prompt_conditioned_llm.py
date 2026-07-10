from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from attrs import define, field

from yuubot.domain import ConversationContext, LLMInput, StreamEvent, StreamStopPayload
from yuubot.runtime.cache import CachePool

from .llm_rules import RuleBuilder, RulePredicate, reply_text


@define
class PromptConditionedProvider:
    """Test provider that emits actions only when LLMInput matches declarative rules."""

    rules: list[tuple[RulePredicate, RuleBuilder]]
    fallback: list[StreamEvent] | None = field(default=None)

    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del model, context, cache, metadata
        if stop_event.is_set():
            yield StreamEvent("stop", "stream_stop", StreamStopPayload("interrupted"))
            return
        for matches, build_events in self.rules:
            if matches(input):
                for event in build_events(input):
                    yield event
                return
        events = self.fallback if self.fallback is not None else reply_text("")(input)
        for event in events:
            yield event

    async def close(self) -> None:
        return None
