"""ProviderPool -- registry of provider configs, client factory, and metrics ring buffer."""

from __future__ import annotations

import os
import re
from collections import deque
from typing import TYPE_CHECKING, Any

import msgspec

from .client import YLLMClient
from .providers.anthropic import AnthropicMessagesProvider
from .providers.openai import OpenAIChatCompletionProvider
from .types import CallRecord, History, Message, ModelBinding, ProviderSpec
from .types import Reasoning, Response, is_text_item

if TYPE_CHECKING:
    from .session import YuuSession


_BAD_TOKENS = ("preview", "beta", "search", "web", "tool", "thinking", "reasoning")


def _score_candidate(selector: str, candidates: list[str]) -> list[str]:
    """Score and sort candidates by relevance to *selector*.

    Pure function — no side effects, no I/O.

    Scoring:
    - +100 for substring match of *selector* in candidate name
    - -10 per bad token (preview, beta, search, web, tool, thinking, reasoning)
    - +3 for ``latest``, +2 for ``stable``
    - Tie-break by version tuple then shorter name length
    """
    selector_l = selector.lower()

    def _key(candidate: str) -> tuple[int, tuple[int, ...], int]:
        candidate_l = candidate.lower()
        score = 0
        if selector_l in candidate_l:
            score += 100
        for token in _BAD_TOKENS:
            if token in candidate_l:
                score -= 10
        if "latest" in candidate_l:
            score += 3
        if "stable" in candidate_l:
            score += 2
        version = tuple(int(part) for part in re.findall(r"\d+", candidate_l))
        return (score, version, -len(candidate))

    return sorted(candidates, key=_key, reverse=True)


def _collect_text(item: object) -> str:
    """Extract text from a :class:`StreamItem`.

    Covers ``Response`` and ``Reasoning`` (both have ``.item``).
    Returns ``""`` for other stream item types.
    """
    if isinstance(item, Response | Reasoning) and is_text_item(item.item):
        return item.item["text"]
    return ""


class ProviderPool:
    """Registry of :class:`ProviderSpec` configs with client factory and metrics.

    Parameters
    ----------
    providers : dict[str, ProviderSpec] | None
        Initial provider registry, keyed by provider name.
    metrics_maxlen : int
        Maximum number of :class:`CallRecord` entries to retain in the
        ring buffer.  Defaults to 1000.
    judge_provider : str | None
        Provider name for the judge LLM used in model resolution.
        Must be a registered provider.
    judge_model : str | None
        Model id for the judge LLM used in model resolution.
        Used together with *judge_provider* to construct the judge
        client via :meth:`get_client`.
    """

    def __init__(
        self,
        providers: dict[str, ProviderSpec] | None = None,
        *,
        metrics_maxlen: int = 1000,
        judge_provider: str | None = None,
        judge_model: str | None = None,
    ) -> None:
        self._providers: dict[str, ProviderSpec] = dict(providers) if providers else {}
        self._binding_cache: dict[tuple[str, str], str] = {}
        self._metrics: deque[CallRecord] = deque(maxlen=metrics_maxlen)
        self._judge_provider = judge_provider
        self._judge_model = judge_model
        self._judge_client: YLLMClient | None = None

    # -- Registry -----------------------------------------------------------

    def register(self, spec: ProviderSpec) -> None:
        """Add or replace a provider in the registry.

        Parameters
        ----------
        spec : ProviderSpec
            Provider configuration to register.  If a provider with the
            same ``name`` already exists, it is replaced.
        """
        self._providers[spec.name] = spec

    # -- Client factory -----------------------------------------------------

    def get_client(self, binding: ModelBinding) -> YLLMClient:
        """Build a :class:`YLLMClient` for *binding* from the matching spec.

        Parameters
        ----------
        binding : ModelBinding
            Resolved binding whose ``provider_name`` selects the spec and
            ``model`` becomes the client's default model.

        Returns
        -------
        YLLMClient
            A new client wired to the provider specified by the binding.

        Raises
        ------
        KeyError
            If *binding.provider_name* is not registered.
        ValueError
            If the spec's ``api_type`` is unsupported.
        """
        spec = self._providers[binding.provider_name]

        # Resolve API key from environment
        api_key: str | None = spec.api_key or None
        if api_key is None and spec.api_key_env:
            api_key = os.environ.get(spec.api_key_env)

        # Resolve base_url (empty string → None)
        base_url: str | None = spec.base_url if spec.base_url else None

        # Safely extract extra kwargs (timeout, max_retries, etc.)
        extra: dict[str, Any] = msgspec.convert(spec.extra, type=dict)

        # Build provider based on api_type
        if spec.api_type == "openai-chat-completion":
            provider = OpenAIChatCompletionProvider(
                api_key=api_key,
                base_url=base_url,
                provider_name=spec.name,
                timeout=extra.get("timeout"),
                max_retries=extra.get("max_retries"),
                default_headers=extra.get("default_headers"),
            )
        elif spec.api_type == "anthropic-messages":
            provider = AnthropicMessagesProvider(
                api_key=api_key,
                base_url=base_url,
                provider_name=spec.name,
                timeout=extra.get("timeout"),
                max_retries=extra.get("max_retries"),
                default_headers=extra.get("default_headers"),
            )
        else:
            raise ValueError(f"unsupported api_type: {spec.api_type!r}")

        return YLLMClient(provider, default_model=binding.model)

    def supports_seamless_recovery(self, provider_name: str) -> bool:
        """Return whether a provider can continue from an assistant prefix."""
        spec = self._providers.get(provider_name)
        if spec is None:
            return False
        return bool(
            spec.extra.get("seamless_recovery")
            or spec.extra.get("assistant_prefix_completion")
        )

    # -- Judge client --------------------------------------------------------

    def _get_judge_client(self) -> YLLMClient | None:
        """Return the cached judge client, creating it lazily from pool config.

        Returns ``None`` if *judge_provider* or *judge_model* is not set.
        """
        if self._judge_client is not None:
            return self._judge_client
        if self._judge_provider is None or self._judge_model is None:
            return None
        binding = ModelBinding(
            provider_name=self._judge_provider,
            model=self._judge_model,
        )
        self._judge_client = self.get_client(binding)
        return self._judge_client

    # -- Session factory -----------------------------------------------------

    def create_session(
        self,
        selector: str,
        history: History | None = None,
    ) -> YuuSession:
        """Create a stateful session bound to a model selector.

        The session uses this pool for model resolution and provider
        fallback.  The pool's judge config (if any) is used for
        LLM-based model selection.

        Parameters
        ----------
        selector : str
            Model selector to resolve (e.g. ``"deepseek"``, ``"claude"``).
        history : History | None
            Optional initial conversation history.

        Returns
        -------
        YuuSession
            A new session ready for ``append()`` and ``stream()`` calls.
        """
        from .session import YuuSession

        return YuuSession(pool=self, selector=selector, history=history)

    # -- Resolution pipeline ------------------------------------------------

    def invalidate(self, selector: str, provider_name: str | None = None) -> None:
        """Remove cached binding(s) for *selector*.

        Parameters
        ----------
        selector : str
            Model selector whose bindings should be evicted.
        provider_name : str | None
            If provided, only the binding for this specific provider is
            removed.  If ``None``, all bindings for *selector* across
            every provider are evicted.
        """
        if provider_name is None:
            keys = [k for k in self._binding_cache if k[0] == selector]
        else:
            keys = [(selector, provider_name)]
        for k in keys:
            self._binding_cache.pop(k, None)

    async def _llm_pick(
        self,
        judge: YLLMClient,
        provider_name: str,
        selector: str,
        candidates: list[str],
    ) -> str:
        """Use *judge* LLM to pick the best candidate model.

        Falls back to :func:`_score_candidate` if the LLM response does not
        contain a valid candidate.
        """
        prompt_text = "\n".join(
            [
                f"Choose the best model for selector {selector!r} on provider {provider_name!r}.",
                "Pick exactly one candidate from the list.",
                "Prefer stable, latest, general-purpose chat models.",
                "Avoid preview, beta, search, web, thinking, reasoning, or "
                "tool-specific variants unless no better option exists.",
                "Return only the exact candidate string.",
                "",
                "Candidates:",
                *[f"- {c}" for c in candidates],
            ]
        )
        msg = Message(role="user", content=[{"type": "text", "text": prompt_text}])
        stream, _store = await judge.stream([msg])
        text = ""
        async for item in stream:
            text += _collect_text(item)
        choice = text.strip().strip("`\"'")
        if choice in candidates:
            return choice
        for candidate in candidates:
            if candidate in choice:
                return candidate
        # fallback to heuristic
        return _score_candidate(selector, candidates)[0]

    async def resolve(
        self,
        selector: str,
        *,
        provider_order: list[str] | None = None,
    ) -> list[ModelBinding]:
        """Resolve *selector* across registered providers.

        For each provider the pipeline is: binding cache → ``list_models()``
        (always fresh) → exact match → substring filter → LLM pick /
        heuristic.  Resolved bindings are cached in ``_binding_cache``.

        When multiple candidates match a provider and the pool has a
        judge configuration (see :class:`ProviderPool`), an LLM is used
        to pick the best candidate.  Otherwise the heuristic
        :func:`_score_candidate` is used directly.

        Parameters
        ----------
        selector : str
            Model selector to resolve (e.g. ``"deepseek"``, ``"claude"``).
        provider_order : list[str] | None
            Order in which providers are tried.  Defaults to registration
            order.

        Returns
        -------
        list[ModelBinding]
            One binding per matching provider.  Each is a viable
            ``(provider, model)`` pair usable for fallback.

        Raises
        ------
        RuntimeError
            If no provider has a model matching *selector*.
        """
        providers = provider_order or list(self._providers.keys())
        bindings: list[ModelBinding] = []
        judge = self._get_judge_client()

        for provider_name in providers:
            cache_key = (selector, provider_name)

            # 1. Check binding cache
            if cache_key in self._binding_cache:
                bindings.append(
                    ModelBinding(
                        provider_name=provider_name,
                        model=self._binding_cache[cache_key],
                        source="cached",
                    )
                )
                continue

            spec = self._providers.get(provider_name)
            if spec is None:
                continue

            # 2. Build client and call list_models() — always fresh
            temp_client = self.get_client(
                ModelBinding(provider_name=provider_name, model="")
            )
            try:
                models = await temp_client.provider.list_models()
            except (ConnectionError, TimeoutError, OSError):
                continue  # transient network error, skip this provider

            # 3. Exact match
            exact = next((m for m in models if m.id == selector), None)
            if exact is not None:
                binding = ModelBinding(provider_name, exact.id, "exact")
                self._binding_cache[cache_key] = exact.id
                bindings.append(binding)
                continue

            # 4. Substring filter
            candidates = [m.id for m in models if selector.lower() in m.id.lower()]
            if not candidates:
                continue

            # 5. Pick best
            if len(candidates) == 1:
                chosen = candidates[0]
                source = "substring"
            elif judge is not None:
                try:
                    chosen = await self._llm_pick(
                        judge, provider_name, selector, candidates
                    )
                    source = "llm_pick"
                except Exception:
                    chosen = _score_candidate(selector, candidates)[0]
                    source = "heuristic"
            else:
                chosen = _score_candidate(selector, candidates)[0]
                source = "heuristic"

            binding = ModelBinding(provider_name, chosen, source)
            self._binding_cache[cache_key] = chosen
            bindings.append(binding)

        if not bindings:
            raise RuntimeError(
                f"no provider has a model matching selector {selector!r}"
            )

        return bindings

    # -- Metrics ------------------------------------------------------------

    def record(self, record: CallRecord) -> None:
        """Append a :class:`CallRecord` to the ring buffer."""
        self._metrics.append(record)

    def metrics(self) -> list[CallRecord]:
        """Return all recorded metrics."""
        return list(self._metrics)

    def metrics_since(self, since: float) -> list[CallRecord]:
        """Return records where ``started_at >= since``."""
        return [r for r in self._metrics if r.started_at >= since]
