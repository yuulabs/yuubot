import asyncio
from collections.abc import AsyncIterator

from attrs import define, field

from ..domain.messages import ConversationContext, LLMInput, ModelCard
from ..domain.stream import StreamEvent, Usage
from ..runtime.cache import CachePool
from ..util.stream import stream_stop_event
from .catalog import merge_catalog
from .protocol import Provider
from .types import AccountSnapshot, ValidationResult


@define
class ScriptedProvider:
    """Deterministic provider replaying pre-scripted event steps; for tests."""

    steps: list[list[StreamEvent]]
    _index: int = field(default=0, init=False)

    async def list_presets(self) -> list[ModelCard]:
        return []

    async def list_remote_models(self) -> list[str]:
        return []

    def merge_catalog(self, presets: list[ModelCard], remote: list[str]) -> list[ModelCard]:
        return merge_catalog(presets, remote)

    async def get_balance(self) -> AccountSnapshot | None:
        return None

    async def validate(self) -> ValidationResult:
        return ValidationResult(ok=True)

    async def stream(
        self,
        input: LLMInput,
        *,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache
        if stop_event.is_set():
            yield stream_stop_event("interrupted", Usage(), {}, cost_estimated=False)
            return
        events = self.steps[min(self._index, len(self.steps) - 1)]
        self._index += 1
        for event in events:
            yield event

    async def close(self) -> None:
        return None


def scripted_reply(text: str) -> ScriptedProvider:
    return ScriptedProvider(
        [
            [
                StreamEvent(group_id="text-1", kind="text_delta", payload={"text": text}),
                stream_stop_event("stop", Usage(), {}, cost_estimated=False),
            ]
        ]
    )
