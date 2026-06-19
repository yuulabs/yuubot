"""Tests for ProviderPool: resolve pipeline, cache, metrics, _score_candidate."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuullm.pool import ProviderPool, _collect_text, _score_candidate
from yuullm.types import (
    CallRecord,
    Message,
    ModelBinding,
    ProviderModel,
    ProviderSpec,
    Response,
    Store,
    Usage,
)


# ---------------------------------------------------------------------------
# ProviderSpec & ModelBinding struct tests
# ---------------------------------------------------------------------------


def test_provider_spec_defaults() -> None:
    """ProviderSpec instantiation with defaults."""
    spec = ProviderSpec(name="test-provider", api_type="openai-chat-completion")

    assert spec.name == "test-provider"
    assert spec.api_type == "openai-chat-completion"
    assert spec.base_url == ""
    assert spec.api_key_env == ""
    assert spec.extra == {}


def test_model_binding_source_variants() -> None:
    """ModelBinding with all five source strings."""
    for source in ("cached", "exact", "substring", "llm_pick", "heuristic"):
        binding = ModelBinding(
            provider_name="deepseek", model="deepseek-v4", source=source
        )
        assert binding.provider_name == "deepseek"
        assert binding.model == "deepseek-v4"
        assert binding.source == source

    # Default source is empty string
    binding_default = ModelBinding(provider_name="openai", model="gpt-4o")
    assert binding_default.source == ""


# ---------------------------------------------------------------------------
# CallRecord & metrics ring buffer tests
# ---------------------------------------------------------------------------


def test_call_record_creation() -> None:
    """CallRecord creation with required and optional fields."""
    t0 = time.monotonic()
    record = CallRecord(
        provider_name="openai",
        model="gpt-4o",
        selector="gpt-4",
        started_at=t0,
    )

    assert record.provider_name == "openai"
    assert record.model == "gpt-4o"
    assert record.selector == "gpt-4"
    assert record.started_at == t0
    assert record.finished_at is None
    assert record.usage is None
    assert record.error is None

    # With optional fields
    usage = Usage(provider="openai", model="gpt-4o", input_tokens=10, output_tokens=20)
    t1 = time.monotonic()
    record_full = CallRecord(
        provider_name="openai",
        model="gpt-4o",
        selector="gpt-4",
        started_at=t0,
        finished_at=t1,
        usage=usage,
        error="timeout",
    )
    assert record_full.finished_at == t1
    assert record_full.usage is usage
    assert record_full.error == "timeout"


def test_metrics_ring_buffer() -> None:
    """Append up to maxlen, verify oldest evicted beyond capacity."""
    pool = ProviderPool(metrics_maxlen=3)

    r1 = CallRecord(provider_name="p1", model="m1", selector="s1", started_at=1.0)
    r2 = CallRecord(provider_name="p2", model="m2", selector="s2", started_at=2.0)
    r3 = CallRecord(provider_name="p3", model="m3", selector="s3", started_at=3.0)
    r4 = CallRecord(provider_name="p4", model="m4", selector="s4", started_at=4.0)

    pool.record(r1)
    pool.record(r2)
    pool.record(r3)
    assert len(pool.metrics()) == 3
    assert pool.metrics() == [r1, r2, r3]

    # Push beyond capacity — r1 should be evicted
    pool.record(r4)
    assert len(pool.metrics()) == 3
    assert pool.metrics() == [r2, r3, r4]


def test_metrics_since_filtering() -> None:
    """metrics_since() returns records with started_at >= threshold."""
    pool = ProviderPool()
    r1 = CallRecord(provider_name="p1", model="m1", selector="s1", started_at=100.0)
    r2 = CallRecord(provider_name="p2", model="m2", selector="s2", started_at=200.0)
    r3 = CallRecord(provider_name="p3", model="m3", selector="s3", started_at=300.0)

    pool.record(r1)
    pool.record(r2)
    pool.record(r3)

    assert pool.metrics_since(since=200.0) == [r2, r3]
    assert pool.metrics_since(since=300.0) == [r3]
    assert pool.metrics_since(since=400.0) == []


def test_metrics_returns_all_in_order() -> None:
    """metrics() returns all records in insertion order."""
    pool = ProviderPool()
    records = [
        CallRecord(provider_name="p1", model="m1", selector="s1", started_at=float(i))
        for i in range(5)
    ]
    for r in records:
        pool.record(r)
    assert pool.metrics() == records


# ---------------------------------------------------------------------------
# _score_candidate pure function tests
# ---------------------------------------------------------------------------


def test_score_candidate_substring_bonus() -> None:
    """Selector in candidate name → +100 points."""
    candidates = ["gpt-4-pro", "claude-3", "gpt-4o"]
    result = _score_candidate("pro", candidates)

    # "gpt-4-pro" gets +100 for substring match; others get 0
    assert result[0] == "gpt-4-pro"


def test_score_candidate_bad_tokens() -> None:
    """Preview/beta/search etc → -10 penalty per token."""
    # Three candidates with one different bad token each, same version
    candidates = ["gpt-4-preview", "gpt-4-beta", "gpt-4-search"]
    result = _score_candidate("gpt-4", candidates)
    # All get +100 (substring), all get -10 (one bad token each)
    # All version (4,), tie-break by length:
    # "gpt-4-beta" (10 chars), "gpt-4-search" (12), "gpt-4-preview" (13)
    # Shorter wins → gpt-4-beta
    assert result[0] == "gpt-4-beta"

    # A candidate with multiple bad tokens loses to single bad token
    candidates2 = ["gpt-4-preview-beta", "gpt-4-preview"]
    result2 = _score_candidate("gpt-4", candidates2)
    # "gpt-4-preview": +100 - 10 = 90
    # "gpt-4-preview-beta": +100 - 10 (preview) - 10 (beta) = 80
    assert result2[0] == "gpt-4-preview"


def test_score_candidate_latest_stable() -> None:
    """'latest' → +3, 'stable' → +2 bonuses."""
    candidates = ["gpt-4", "gpt-4-latest", "gpt-4-stable"]
    result = _score_candidate("gpt-4", candidates)

    # All get +100 (substring), version (4,)
    # "gpt-4": 100
    # "gpt-4-latest": 100 + 3 = 103
    # "gpt-4-stable": 100 + 2 = 102
    assert result[0] == "gpt-4-latest"
    assert result[1] == "gpt-4-stable"
    assert result[2] == "gpt-4"


def test_score_candidate_version_tiebreak() -> None:
    """Higher version numbers win on tie scores."""
    candidates = ["gpt-3.5-turbo", "gpt-4", "gpt-4o"]
    result = _score_candidate("gpt", candidates)

    # All get +100 (substring), no bad tokens
    # Versions: gpt-3.5-turbo → (3, 5), gpt-4 → (4,), gpt-4o → (4,)
    # gpt-4 and gpt-4o tie at version (4,), tie-break by length: gpt-4 (5) < gpt-4o (6)
    # So order: gpt-4, gpt-4o, gpt-3.5-turbo
    assert result[0] == "gpt-4"
    assert result[1] == "gpt-4o"
    assert result[2] == "gpt-3.5-turbo"


def test_score_candidate_length_tiebreak() -> None:
    """Shorter names win when score and version are identical."""
    # "gpt-4o" and "gpt-4o-mini" both have only digit "4" → version (4,)
    # "gpt-4o" length 6, "gpt-4o-mini" length 12 → shorter wins
    candidates = ["gpt-4o-mini", "gpt-4o"]
    result = _score_candidate("gpt", candidates)

    assert result[0] == "gpt-4o"
    assert result[1] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Client factory tests
# ---------------------------------------------------------------------------


def test_register_and_get_client_openai() -> None:
    """Register OpenAI spec, get_client() returns YLLMClient with correct provider."""
    spec = ProviderSpec(
        name="deepseek",
        api_type="openai-chat-completion",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
    )
    pool = ProviderPool()
    pool.register(spec)

    binding = ModelBinding(provider_name="deepseek", model="deepseek-v4-pro")
    client = pool.get_client(binding)

    assert client.default_model == "deepseek-v4-pro"
    assert client.provider.api_type == "openai-chat-completion"
    assert client.provider.provider == "deepseek"


def test_register_and_get_client_anthropic() -> None:
    """Register Anthropic spec, get_client() returns YLLMClient with correct provider."""
    spec = ProviderSpec(
        name="anthropic",
        api_type="anthropic-messages",
        base_url="",
        api_key_env="ANTHROPIC_API_KEY",
    )
    pool = ProviderPool()
    pool.register(spec)

    binding = ModelBinding(provider_name="anthropic", model="claude-sonnet-4-20250514")
    client = pool.get_client(binding)

    assert client.default_model == "claude-sonnet-4-20250514"
    assert client.provider.api_type == "anthropic-messages"
    assert client.provider.provider == "anthropic"


def test_get_client_unsupported_api_type() -> None:
    """ValueError when spec.api_type is unknown."""
    spec = ProviderSpec(
        name="bad-provider",
        api_type="grpc-something-unknown",
    )
    pool = ProviderPool()
    pool.register(spec)

    binding = ModelBinding(provider_name="bad-provider", model="some-model")
    with pytest.raises(ValueError, match="unsupported api_type"):
        pool.get_client(binding)


def test_get_client_missing_provider() -> None:
    """KeyError when binding references unregistered provider."""
    pool = ProviderPool()
    binding = ModelBinding(provider_name="nonexistent", model="some-model")

    with pytest.raises(KeyError):
        pool.get_client(binding)


# ---------------------------------------------------------------------------
# Resolve pipeline tests
# ---------------------------------------------------------------------------


def _mock_get_client_for_models(
    pool: ProviderPool,
    models_by_provider: dict[str, list[ProviderModel]],
) -> dict[str, AsyncMock]:
    """Replace pool.get_client() to return mock clients with controlled list_models().

    Returns a dict of ``provider_name → mock_list_models`` for call-count assertions.
    Mock lists are created once and reused across get_client() calls.
    """
    mock_lists: dict[str, AsyncMock] = {
        name: AsyncMock(return_value=models)
        for name, models in models_by_provider.items()
    }

    def _fake_get_client(binding: ModelBinding) -> MagicMock:
        provider_name = binding.provider_name
        mock_provider = MagicMock()
        mock_provider.list_models = mock_lists.get(
            provider_name, AsyncMock(return_value=[])
        )
        mock_client = MagicMock()
        mock_client.provider = mock_provider
        return mock_client

    pool.get_client = _fake_get_client  # type: ignore[method-assign]
    return mock_lists


@pytest.mark.asyncio
async def test_resolve_exact_match() -> None:
    """resolve() with exact model ID match returns source='exact'."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [
                ProviderModel(id="gpt-4"),
                ProviderModel(id="gpt-4o"),
                ProviderModel(id="gpt-4-turbo"),
            ],
        },
    )

    bindings = await pool.resolve("gpt-4o")

    assert len(bindings) == 1
    assert bindings[0].provider_name == "openai"
    assert bindings[0].model == "gpt-4o"
    assert bindings[0].source == "exact"


@pytest.mark.asyncio
async def test_resolve_substring_single_candidate() -> None:
    """resolve() with single substring match returns source='substring'."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [
                ProviderModel(id="gpt-4"),
                ProviderModel(id="claude-3-opus"),
                ProviderModel(id="claude-3-sonnet"),
            ],
        },
    )

    # "opus" only matches claude-3-opus → single candidate
    bindings = await pool.resolve("opus")

    assert len(bindings) == 1
    assert bindings[0].model == "claude-3-opus"
    assert bindings[0].source == "substring"


@pytest.mark.asyncio
async def test_resolve_cache_hit() -> None:
    """Second resolve() returns cached binding without calling list_models()."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )
    mocks = _mock_get_client_for_models(
        pool,
        {
            "openai": [ProviderModel(id="gpt-4"), ProviderModel(id="gpt-4o")],
        },
    )

    # First call — populates cache
    bindings1 = await pool.resolve("gpt-4o")
    assert bindings1[0].source == "exact"
    call_count_after_first = mocks["openai"].call_count

    # Second call — should use cache
    bindings2 = await pool.resolve("gpt-4o")
    assert bindings2[0].source == "cached"
    assert bindings2[0].model == "gpt-4o"
    # list_models should NOT have been called again
    assert mocks["openai"].call_count == call_count_after_first


@pytest.mark.asyncio
async def test_resolve_cache_miss() -> None:
    """After invalidate(), resolve() calls list_models() again."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )
    mocks = _mock_get_client_for_models(
        pool,
        {
            "openai": [ProviderModel(id="gpt-4"), ProviderModel(id="gpt-4o")],
        },
    )

    # First call populates cache
    await pool.resolve("gpt-4")
    call_count = mocks["openai"].call_count

    # Invalidate the cached entry
    pool.invalidate("gpt-4", "openai")

    # Second call should call list_models() again
    await pool.resolve("gpt-4")
    assert mocks["openai"].call_count == call_count + 1


def test_invalidate_exact_provider() -> None:
    """invalidate(selector, provider) removes exact cache entry."""
    pool = ProviderPool()

    # Manually populate cache to simulate a previous resolution
    pool._binding_cache[("gpt-4", "openai")] = "gpt-4"
    pool._binding_cache[("gpt-4", "deepseek")] = "deepseek-chat"
    pool._binding_cache[("claude", "anthropic")] = "claude-sonnet"

    pool.invalidate("gpt-4", "openai")

    assert ("gpt-4", "openai") not in pool._binding_cache
    assert ("gpt-4", "deepseek") in pool._binding_cache
    assert ("claude", "anthropic") in pool._binding_cache


def test_invalidate_all_providers() -> None:
    """invalidate(selector) with no provider removes all entries for selector."""
    pool = ProviderPool()

    pool._binding_cache[("gpt-4", "openai")] = "gpt-4"
    pool._binding_cache[("gpt-4", "deepseek")] = "deepseek-chat"
    pool._binding_cache[("claude", "anthropic")] = "claude-sonnet"

    pool.invalidate("gpt-4")  # provider_name=None → all for selector

    assert ("gpt-4", "openai") not in pool._binding_cache
    assert ("gpt-4", "deepseek") not in pool._binding_cache
    assert ("claude", "anthropic") in pool._binding_cache  # untouched


@pytest.mark.asyncio
async def test_resolve_no_match_raises() -> None:
    """RuntimeError when no provider has any model matching selector."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [ProviderModel(id="gpt-4"), ProviderModel(id="gpt-4o")],
        },
    )

    with pytest.raises(RuntimeError, match="no provider has a model matching selector"):
        await pool.resolve("claude")


@pytest.mark.asyncio
async def test_resolve_multi_candidate_llm_pick() -> None:
    """Pool with judge config uses LLM pick for multiple substring matches."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        },
        judge_provider="openai",
        judge_model="gpt-4o",
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [
                ProviderModel(id="gpt-4"),
                ProviderModel(id="gpt-4o"),
                ProviderModel(id="gpt-4-turbo"),
                ProviderModel(id="gpt-4o-mini"),
                ProviderModel(id="claude-3"),
            ],
        },
    )

    # Create a mock judge that "picks" gpt-4o
    async def _mock_stream():
        yield Response(item={"type": "text", "text": "gpt-4o"})

    mock_judge = MagicMock()
    mock_judge.stream = AsyncMock(return_value=(_mock_stream(), Store()))

    # Override _get_judge_client to return our mock
    pool._get_judge_client = MagicMock(return_value=mock_judge)

    # selector "gpt" matches all gpt models (substring, not exact)
    bindings = await pool.resolve("gpt")

    assert len(bindings) == 1
    assert bindings[0].model == "gpt-4o"
    assert bindings[0].source == "llm_pick"

    # Verify judge was called
    mock_judge.stream.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_llm_pick_fallback() -> None:
    """Pool judge that errors → falls back to _score_candidate heuristic."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        },
        judge_provider="openai",
        judge_model="gpt-4o",
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [ProviderModel(id="gpt-4-preview"), ProviderModel(id="gpt-4")],
        },
    )

    # Judge that always raises
    mock_judge = MagicMock()
    mock_judge.stream = AsyncMock(side_effect=RuntimeError("judge unavailable"))

    pool._get_judge_client = MagicMock(return_value=mock_judge)

    # selector "gpt" matches both (substring, not exact)
    bindings = await pool.resolve("gpt")

    assert len(bindings) == 1
    # gpt-4 should win because gpt-4-preview has "preview" bad token penalty
    assert bindings[0].model == "gpt-4"
    assert bindings[0].source == "heuristic"


@pytest.mark.asyncio
async def test_resolve_multi_provider_ordered() -> None:
    """resolve() follows provider_order and returns bindings for each matching provider."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
            "anthropic": ProviderSpec(name="anthropic", api_type="anthropic-messages"),
        }
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [ProviderModel(id="gpt-4"), ProviderModel(id="gpt-4o")],
            "anthropic": [
                ProviderModel(id="claude-3-opus"),
                ProviderModel(id="claude-3-sonnet"),
            ],
        },
    )

    bindings = await pool.resolve("gpt", provider_order=["openai", "anthropic"])

    assert len(bindings) == 1  # anthropic has no "gpt" match
    assert bindings[0].provider_name == "openai"

    # Test with selector matching both
    bindings2 = await pool.resolve("4", provider_order=["openai", "anthropic"])
    # "4" matches "gpt-4" and "gpt-4o" on openai (multi-candidate → heuristic),
    # "4" is NOT in "claude-3-opus" or "claude-3-sonnet" → no anthropic match
    assert len(bindings2) == 1
    assert bindings2[0].provider_name == "openai"

    # With "opus" it should match anthropic only
    bindings3 = await pool.resolve("opus", provider_order=["openai", "anthropic"])
    assert len(bindings3) == 1
    assert bindings3[0].provider_name == "anthropic"
    assert bindings3[0].model == "claude-3-opus"


@pytest.mark.asyncio
async def test_resolve_skips_unavailable_provider() -> None:
    """Provider whose list_models() raises a transient network error is skipped."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )

    def _failing_get_client(binding: ModelBinding) -> MagicMock:
        mock_provider = MagicMock()
        mock_provider.list_models = AsyncMock(
            side_effect=ConnectionError("API unavailable")
        )
        mock_client = MagicMock()
        mock_client.provider = mock_provider
        return mock_client

    pool.get_client = _failing_get_client  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="no provider has a model matching selector"):
        await pool.resolve("gpt-4")


@pytest.mark.asyncio
async def test_resolve_config_error_propagates() -> None:
    """get_client() failure (misconfiguration) propagates, not silently skipped."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="unsupported-type"),
        }
    )

    with pytest.raises(ValueError, match="unsupported api_type"):
        await pool.resolve("gpt-4")


@pytest.mark.asyncio
async def test_resolve_no_judge_uses_heuristic() -> None:
    """When judge is None, multi-candidate uses _score_candidate directly."""
    pool = ProviderPool(
        {
            "openai": ProviderSpec(name="openai", api_type="openai-chat-completion"),
        }
    )
    _mock_get_client_for_models(
        pool,
        {
            "openai": [
                ProviderModel(id="gpt-4-preview"),
                ProviderModel(id="gpt-4"),
                ProviderModel(id="gpt-4-turbo"),
            ],
        },
    )

    # No judge provided → should use heuristic
    # selector "gpt" matches all three (substring, not exact)
    bindings = await pool.resolve("gpt")

    assert len(bindings) == 1
    # gpt-4 wins over gpt-4-preview (bad token) and gpt-4-turbo (length tiebreak vs gpt-4)
    assert bindings[0].model == "gpt-4"
    assert bindings[0].source == "heuristic"


# ---------------------------------------------------------------------------
# _collect_text tests
# ---------------------------------------------------------------------------


def test_collect_text_from_response() -> None:
    """_collect_text extracts text from Response items."""
    item = Response(item={"type": "text", "text": "hello world"})
    assert _collect_text(item) == "hello world"


def test_collect_text_from_reasoning() -> None:
    """_collect_text extracts text from Reasoning items."""
    from yuullm.types import Reasoning

    item = Reasoning(item={"type": "text", "text": "let me think..."})
    assert _collect_text(item) == "let me think..."


def test_collect_text_other_types_return_empty() -> None:
    """_collect_text returns '' for items without .item attribute."""
    assert _collect_text("plain string") == ""
    assert _collect_text(42) == ""
    assert _collect_text(None) == ""


# ---------------------------------------------------------------------------
# ProviderPool judge config & create_session tests
# ---------------------------------------------------------------------------


def test_pool_judge_config_stored() -> None:
    """ProviderPool stores judge_provider and judge_model."""
    pool = ProviderPool(
        judge_provider="deepseek",
        judge_model="deepseek-chat",
    )
    assert pool._judge_provider == "deepseek"
    assert pool._judge_model == "deepseek-chat"
    assert pool._judge_client is None


def test_pool_judge_config_defaults() -> None:
    """ProviderPool judge_provider and judge_model default to None."""
    pool = ProviderPool()
    assert pool._judge_provider is None
    assert pool._judge_model is None


def test_get_judge_client_returns_none_without_config() -> None:
    """_get_judge_client() returns None when judge config is not set."""
    pool = ProviderPool()
    assert pool._get_judge_client() is None


def test_get_judge_client_creates_and_caches() -> None:
    """_get_judge_client() creates client via get_client and caches the result."""
    pool = ProviderPool(
        {
            "deepseek": ProviderSpec(
                name="deepseek",
                api_type="openai-chat-completion",
            ),
        },
        judge_provider="deepseek",
        judge_model="deepseek-chat",
    )

    mock_client = MagicMock()
    pool.get_client = MagicMock(return_value=mock_client)

    result1 = pool._get_judge_client()
    result2 = pool._get_judge_client()

    assert result1 is mock_client
    assert result2 is mock_client
    # get_client should only be called once (cached)
    pool.get_client.assert_called_once_with(
        ModelBinding(provider_name="deepseek", model="deepseek-chat")
    )


def test_pool_create_session_returns_yuusession() -> None:
    """ProviderPool.create_session() returns a YuuSession with correct attributes."""
    from yuullm.session import YuuSession

    pool = ProviderPool()
    session = pool.create_session(selector="deepseek")

    assert isinstance(session, YuuSession)
    assert session._pool is pool
    assert session._selector == "deepseek"
    assert session._history == []


def test_pool_create_session_with_history() -> None:
    """ProviderPool.create_session() passes history through to YuuSession."""
    pool = ProviderPool()
    history = [Message(role="user", content=[{"type": "text", "text": "hi"}])]

    session = pool.create_session(selector="claude", history=history)

    assert len(session.history) == 1
    assert session.history[0].role == "user"
