"""Provider protocol -- the contract every LLM backend must satisfy."""

from __future__ import annotations

from typing import Protocol

from .types import History, ProviderModel, RawChunkHook, StreamResult


class Provider(Protocol):
    """Unified interface for LLM providers.

    Each provider combines two orthogonal concepts:

    - **api_type**: the wire protocol used (e.g. ``"openai-chat-completion"``,
      ``"openai-responses"``, ``"anthropic-messages"``).
    - **provider**: the vendor / supplier name (e.g. ``"openai"``,
      ``"deepseek"``, ``"openrouter"``, ``"anthropic"``).

    Implementors must supply :meth:`stream` which returns an async iterator
    of provider stream items together with a mutable :class:`Store`.  After
    the iterator is exhausted the store should contain at least ``usage``.
    If the provider can report cost directly (e.g. OpenRouter), it should
    also set ``provider_cost``.  Stateful sessions may wrap provider streams
    with additional control events such as ``AttemptRecovery``.

    Tool specs are represented as a history item via ``yuullm.tools([...])``.
    No ToolSpec class is needed.
    """

    @property
    def api_type(self) -> str:
        """Wire protocol identifier.

        One of ``"openai-chat-completion"``, ``"openai-responses"``,
        ``"anthropic-messages"``.
        """
        ...

    @property
    def provider(self) -> str:
        """Vendor / supplier name (e.g. ``"openai"``, ``"deepseek"``)."""
        ...

    async def list_models(self) -> list[ProviderModel]:
        """Return the provider's currently available models."""
        ...

    async def stream(
        self,
        history: History,
        *,
        model: str,
        on_raw_chunk: RawChunkHook | None = None,
        **kwargs,
    ) -> StreamResult:
        """Start a streaming completion.

        Returns
        -------
        iterator : AsyncIterator[StreamItem]
            Yields provider-normalized stream fragments.
        store : Store
            Mutable store populated with usage and optional provider cost.
        """
        ...
