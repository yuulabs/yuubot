from __future__ import annotations

from unittest.mock import patch

import yuullm

from yuubot.characters import get_character
from yuubot.daemon.actor import _build_llm_options, build_definition
from yuubot.daemon.llm_factory import make_llm, make_summary_llm
from yuubot.model_resolution import ModelResolver, build_llm_client


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

    resolved = await resolver.resolve_agent("yuu")
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


async def test_effort_suffix_is_not_sent_as_part_of_model_name(yuubot_config) -> None:
    yuubot_config.agent_llm_refs["yuu"] = "test/test-model:none"
    captured: dict[str, object] = {}

    async def _fake_stream(self, messages, *, model=None, tools=None, **kw):
        del self, messages, tools
        captured["model"] = model
        captured["kwargs"] = kw

        async def _iter():
            if False:
                yield None

        return _iter(), yuullm.Store()

    resolved = await ModelResolver(yuubot_config).resolve_agent("yuu")
    assert resolved.resolved_model == "test-model"
    assert resolved.effort == "none"

    llm = await make_llm("yuu", yuubot_config)
    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider,
        "stream",
        _fake_stream,
    ):
        stream, _store = await llm.stream([yuullm.user("hi")])
        async for _item in stream:
            pass

    assert captured["model"] == "test-model"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["reasoning_effort"] == "none"


async def test_raw_llm_client_model_suffix_sets_reasoning_effort(yuubot_config) -> None:
    captured: dict[str, object] = {}

    async def _fake_stream(self, messages, *, model=None, tools=None, **kw):
        del self, messages, tools
        captured["model"] = model
        captured["kwargs"] = kw

        async def _iter():
            if False:
                yield None

        return _iter(), yuullm.Store()

    llm = build_llm_client("test", "test-model:none", yuubot_config)
    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider,
        "stream",
        _fake_stream,
    ):
        stream, _store = await llm.stream([yuullm.user("hi")])
        async for _item in stream:
            pass

    assert llm.default_model == "test-model"
    assert captured["model"] == "test-model"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["reasoning_effort"] == "none"


async def test_deepseek_none_effort_disables_thinking(yuubot_config) -> None:
    captured: dict[str, object] = {}

    async def _fake_stream(self, messages, *, model=None, tools=None, **kw):
        del self, messages, tools
        captured["model"] = model
        captured["kwargs"] = kw

        async def _iter():
            if False:
                yield None

        return _iter(), yuullm.Store()

    llm = build_llm_client("deepseek", "deepseek-v4-flash:none", yuubot_config)
    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider,
        "stream",
        _fake_stream,
    ):
        stream, _store = await llm.stream(
            [yuullm.user("hi")],
            reasoning_effort="none",
        )
        async for _item in stream:
            pass

    assert llm.default_model == "deepseek-v4-flash"
    assert captured["model"] == "deepseek-v4-flash"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "thinking" not in kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kwargs


async def test_deepseek_non_none_effort_enables_thinking(yuubot_config) -> None:
    captured: dict[str, object] = {}

    async def _fake_stream(self, messages, *, model=None, tools=None, **kw):
        del self, messages, tools
        captured["model"] = model
        captured["kwargs"] = kw

        async def _iter():
            if False:
                yield None

        return _iter(), yuullm.Store()

    llm = build_llm_client("deepseek", "deepseek-v4-pro:high", yuubot_config)
    with patch.object(
        yuullm.providers.OpenAIChatCompletionProvider,
        "stream",
        _fake_stream,
    ):
        stream, _store = await llm.stream([yuullm.user("hi")])
        async for _item in stream:
            pass

    assert llm.default_model == "deepseek-v4-pro"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "thinking" not in kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "high"


def test_yuuagents_definition_strips_effort_suffix_from_model(yuubot_config) -> None:
    yuubot_config.agent_llm_refs["yuu"] = "test/test-model:none"

    options = _build_llm_options(yuubot_config, "yuu")
    definition = build_definition(get_character("yuu"), options, "master")

    assert options["model"] == "test-model"
    assert options["reasoning_effort"] == "none"
    assert definition.llm.stream_kwargs()["model"] == "test-model"
    assert definition.llm.stream_kwargs()["reasoning_effort"] == "none"
