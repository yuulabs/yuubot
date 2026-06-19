"""YLLMClient -- user-facing entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from .cache_config import CacheConfig
from .pricing import PriceCalculator
from .provider import Provider
from .types import (
    Cost,
    History,
    ProviderModel,
    RawChunkHook,
    Store,
    StreamItem,
    StreamResult,
    Usage,
)

if TYPE_CHECKING:
    from .pool import ProviderPool
    from .session import YuuSession


class YLLMClient:
    """Unified LLM client.

    Wraps a :class:`Provider` and an optional :class:`PriceCalculator`,
    exposing a simple ``stream()`` method that returns standardised
    ``StreamItem`` objects and populates a *store* dict with ``Usage``
    and ``Cost`` after the stream is exhausted.

    Tool specs are represented as a history item via ``yuullm.tools([...])``.
    No ToolSpec class is needed.

    Parameters
    ----------
    auto_prompt_caching : bool
        When ``True`` (default), the provider receives *cache_config*
        and *price_calculator* so it can automatically inject
        vendor-specific cache markers.  Set to ``False`` to disable.
    cache_config : CacheConfig | None
        Business-level caching intent passed to the provider.
        Defaults to ``CacheConfig()`` when *auto_prompt_caching* is
        ``True`` and no explicit config is given.
    """

    def __init__(
        self,
        provider: Provider,
        default_model: str,
        price_calculator: PriceCalculator | None = None,
        *,
        auto_prompt_caching: bool = True,
        cache_config: CacheConfig | None = None,
    ) -> None:
        self.default_model = default_model
        self.price_calculator = price_calculator

        # Wire up cache_config into the provider if it supports it
        if auto_prompt_caching:
            effective_config = cache_config or CacheConfig()
            _inject_cache_config(provider, effective_config, price_calculator)

        self.provider = provider

    async def stream(
        self,
        history: History,
        *,
        model: str | None = None,
        on_raw_chunk: RawChunkHook | None = None,
        **kwargs,
    ) -> StreamResult:
        """Start a streaming completion.

        After the returned async iterator is fully consumed the *store*
        dict will contain:

        - ``"usage"`` -- :class:`Usage`
        - ``"cost"``  -- :class:`Cost` | ``None``
        """
        effective_model = model or self.default_model

        iterator, store = await self.provider.stream(
            history,
            model=effective_model,
            on_raw_chunk=on_raw_chunk,
            **kwargs,
        )

        wrapped = self._wrap_iterator(iterator, store)
        return wrapped, store

    async def list_models(self) -> list[ProviderModel]:
        """Return the provider's currently available models."""
        return await self.provider.list_models()

    def create_session(
        self,
        pool: ProviderPool,
        selector: str,
        *,
        history: History | None = None,
    ) -> YuuSession:
        """Create a stateful session bound to a model selector.

        The session owns its conversation history and handles
        multi-provider fallback transparently during
        :meth:`YuuSession.stream`.

        Parameters
        ----------
        pool : ProviderPool
            Provider registry with model resolution cache.
        selector : str
            Logical model selector (e.g. ``"deepseek-v4-pro:max"``).
        history : History | None
            Optional initial conversation history.

        Returns
        -------
        YuuSession
            A new session ready for ``append()`` and ``stream()`` calls.
        """
        from .session import YuuSession

        return YuuSession(
            pool=pool,
            selector=selector,
            history=history,
        )

    async def _wrap_iterator(
        self,
        iterator: AsyncIterator[StreamItem],
        store: Store,
    ) -> AsyncIterator[StreamItem]:
        """Yield items from the provider, then compute cost."""
        async for item in iterator:
            yield item

        # After stream is exhausted, compute cost
        usage: Usage | None = store.usage
        if usage is not None and self.price_calculator is not None:
            provider_cost: float | None = store.provider_cost
            cost: Cost | None = self.price_calculator.calculate(
                usage, provider_cost=provider_cost
            )
            store.cost = cost


def _inject_cache_config(
    provider: Provider,
    config: CacheConfig,
    price_calc: PriceCalculator | None,
) -> None:
    """Configure prompt caching on providers that support it.

    Providers opt in by exposing a public ``configure_cache`` method.
    No-op for providers that don't (e.g. DeepSeek via
    OpenAIChatCompletionProvider with provider_name="deepseek").
    """
    configure = getattr(provider, "configure_cache", None)
    if configure is not None:
        configure(config, price_calc)
