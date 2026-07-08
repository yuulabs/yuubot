from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from attrs import define, field

from yuubot.domain import ConversationContext, LLMInput, ModelCard, StreamEvent, StreamStopPayload
from yuubot.llm import merge_catalog
from yuubot.llm.types import AccountSnapshot, ValidationResult
from yuubot.runtime.cache import CachePool

from .llm_rules import RuleBuilder, RulePredicate, reply_text


@define
class PromptConditionedProvider:
    """Test provider that emits actions only when LLMInput matches declarative rules."""

    rules: list[tuple[RulePredicate, RuleBuilder]]
    fallback: list[StreamEvent] | None = field(default=None)

    async def list_presets(self) -> list[ModelCard]:
        return []

    async def list_remote_models(self) -> list[str]:
        return []

    def merge_catalog(self, presets: list[ModelCard], remote: list[str]) -> list[ModelCard]:
        return merge_catalog(presets, remote)

    async def get_balance(self) -> AccountSnapshot | None:
        return None

    async def validate(self) -> ValidationResult:
        return ValidationResult(True)

    async def stream(
        self,
        input: LLMInput,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        del model, context, cache
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
