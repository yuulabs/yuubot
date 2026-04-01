from __future__ import annotations

from yuubot.daemon.llm_factory import make_summary_llm
from yuubot.model_resolution import ModelResolver


def test_internal_llm_roles_have_expected_defaults(yuubot_config) -> None:
    assert yuubot_config.llm_roles["selector"] == "deepseek-chat"
    assert yuubot_config.llm_roles["summarizer"] == "deepseek-chat"
    assert yuubot_config.llm_roles["vision"] == "gemini-3.1-flash-lite-preview"


async def test_selector_manual_binding_wins_and_refresh_clears_cache(
    yuubot_config,
) -> None:
    resolver = ModelResolver(yuubot_config)

    auto = await resolver.resolve_ref("or/sonnet")
    assert auto.resolved_ref == "openrouter/anthropic/claude-sonnet-4.1"
    assert auto.family == "claude"
    assert auto.supports_vision is True
    assert resolver.store.get("sonnet").auto_cache["openrouter"] == auto.resolved_model

    resolver.bind_resolved(auto, "sonnet")
    resolver.store.set_auto_cache(
        "sonnet", "openrouter", "anthropic/claude-sonnet-4.0-preview", "claude"
    )

    manual = await resolver.resolve_ref("or/sonnet")
    assert manual.source == "manual"
    assert manual.resolved_ref == "openrouter/anthropic/claude-sonnet-4.1"

    resolver.refresh("sonnet")
    state = resolver.store.get("sonnet")
    assert state is not None
    assert state.auto_cache == {}
    assert state.manual_bindings["openrouter"] == "anthropic/claude-sonnet-4.1"

    resolver.bind_resolved(await resolver.resolve_ref("test/test-model"), "sonnet")
    resolver.delete("or/sonnet")
    state = resolver.store.get("sonnet")
    assert state is not None
    assert "openrouter" not in state.manual_bindings
    assert "openrouter" not in state.auto_cache
    assert state.manual_bindings["test"] == "test-model"


async def test_agent_resolution_uses_agent_llm_refs(yuubot_config) -> None:
    resolver = ModelResolver(yuubot_config)

    resolved = await resolver.resolve_agent("main")
    assert resolved.resolved_provider == "test"
    assert resolved.resolved_model == "test-model"


async def test_role_resolution_uses_priority_affinity_and_sticky_provider(
    yuubot_config,
) -> None:
    resolver = ModelResolver(yuubot_config)

    vision = await resolver.resolve_role("vision")
    assert vision.resolved_provider == "aihubmix"
    assert vision.resolved_model == "google/gemini-3.1-flash-lite-preview"

    resolver.provider_priorities["openrouter"] = 999
    sticky = await resolver.resolve_role("vision")
    assert sticky.resolved_provider == "aihubmix"

    resolver.refresh_role("vision")
    refreshed = await resolver.resolve_role("vision")
    assert refreshed.resolved_provider == "openrouter"


async def test_role_resolution_honors_affinity_for_deepseek_selector(
    yuubot_config,
) -> None:
    resolver = ModelResolver(yuubot_config)

    resolved = await resolver.resolve_role("selector")
    assert resolved.resolved_provider == "deepseek"
    assert resolved.resolved_model == "deepseek-chat"
    assert resolved.family == "deepseek"


async def test_summarizer_llm_uses_role_resolution(yuubot_config) -> None:
    llm = await make_summary_llm(yuubot_config)

    assert llm.default_model == "deepseek-chat"
