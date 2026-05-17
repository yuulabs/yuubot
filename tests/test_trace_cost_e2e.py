"""E2E coverage for trace/cost backend wiring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import msgspec
import pytest
import yuullm
import yuutrace
import yuuagents.stage as yuuagents_stage
from yuuagents import Budget, UsageSink
from yuuagents.eventbus import EventBus

from helpers import (
    assert_cost_event,
    assert_tool_usage,
    insert_echo_actor_resources,
)
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.actors import SimpleLoopActor
from yuubot.core.assembly import start_yuuagents_actor
from yuubot.core.bindings import load_actor_binding
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.observability import TraceObserver
from yuubot.core.validation import ConfigurationError
from yuubot.resources.records import (
    BudgetPolicy,
    PricingEntry,
    PricingTable,
    YuuAgentBudget,
)
from yuubot.resources.store.models import ActorORM, LLMBackendORM
from yuubot.runtime.daemon import YuubotDaemon, build_daemon


SOURCE_PATH = "channels/trace-test"
ACTOR_ID = "trace-test-actor"
INTEGRATION_ID = "trace-test-echo"
MESSAGE_ID = "trace-msg-1"
SENDER_ID = "trace-user"
ORIGINAL_TEXT = "hello trace"


async def test_llm_usage_and_priced_cost_recorded_in_trace(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider()
    monkeypatch.setitem(yuuagents_stage._PROVIDER_CLASSES, "openai", lambda **_: llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    store = yuutrace.init_memory()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
        )
        pricing = PricingTable(
            entries=(
                PricingEntry(
                    model="gpt-4",
                    input_per_million=1.0,
                    output_per_million=2.0,
                ),
            )
        )
        await daemon.resources.repository.update(
            LLMBackendORM,
            resources.llm_backend.id,
            pricing=msgspec.to_builtins(pricing),
        )
        await daemon.resources.event_bus.drain()

        async with _client(daemon) as client:
            response = await client.post(
                "/integration/echo",
                json={
                    "integration_id": resources.integration.id,
                    "message_id": MESSAGE_ID,
                    "sender_id": SENDER_ID,
                    "sender_name": "Trace User",
                    "kind": "private",
                    "text": ORIGINAL_TEXT,
                    "segments": [{"kind": "text", "text": ORIGINAL_TEXT}],
                },
            )
        assert response.status_code == 202, response.json()

        actor = daemon.actors.running_actor(resources.actor.id)
        assert isinstance(actor, SimpleLoopActor)
        await actor.next_turn_result()

        result = store.list_conversations()
        assert result["total"] >= 1, "expected at least one conversation trace"

        conv_id = result["conversations"][0]["id"]
        conv = store.get_conversation(conv_id)
        assert conv is not None

        all_event_names = [
            ev["name"]
            for span in conv["spans"]
            for ev in span["events"]
        ]
        assert "yuu.llm.usage" in all_event_names, (
            f"expected yuu.llm.usage event in trace, got: {all_event_names}"
        )
        turn_span = conv["spans"][-1]
        assert_cost_event(turn_span, category="llm", amount=0.00002)
    finally:
        await daemon.stop()


async def test_pricing_check_raises_when_budget_set_without_pricing(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider()
    monkeypatch.setitem(yuuagents_stage._PROVIDER_CLASSES, "openai", lambda **_: llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
        )
        # Patch actor to have max_usd budget but no pricing
        await daemon.resources.repository.update(
            ActorORM,
            resources.actor.id,
            budget=msgspec.to_builtins(YuuAgentBudget(max_usd=1.0)),
        )
        await daemon.resources.event_bus.drain()

        binding = await load_actor_binding(
            daemon.resources.repository,
            resources.actor.id,
            workspace_path=tmp_path / "ws",
        )
        with pytest.raises(ConfigurationError, match="USD budget requires pricing"):
            start_yuuagents_actor(binding, yuuagents_config=yuubot_config.yuuagents)
    finally:
        await daemon.stop()


async def test_pricing_check_raises_when_backend_budget_set_without_pricing(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider()
    monkeypatch.setitem(yuuagents_stage._PROVIDER_CLASSES, "openai", lambda **_: llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
        )
        await daemon.resources.repository.update(
            LLMBackendORM,
            resources.llm_backend.id,
            budget=msgspec.to_builtins(BudgetPolicy(daily_usd=1.0)),
        )
        await daemon.resources.event_bus.drain()

        binding = await load_actor_binding(
            daemon.resources.repository,
            resources.actor.id,
            workspace_path=tmp_path / "ws",
        )
        with pytest.raises(ConfigurationError, match="USD budget requires pricing"):
            start_yuuagents_actor(binding, yuuagents_config=yuubot_config.yuuagents)
    finally:
        await daemon.stop()


async def test_pricing_check_passes_when_pricing_entry_exists(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider()
    monkeypatch.setitem(yuuagents_stage._PROVIDER_CLASSES, "openai", lambda **_: llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
        )
        # Patch backend to have pricing for gpt-4, and actor to have max_usd
        pricing = PricingTable(entries=(PricingEntry(model="gpt-4", input_per_million=1.0, output_per_million=2.0),))
        await daemon.resources.repository.update(
            LLMBackendORM,
            resources.llm_backend.id,
            pricing=msgspec.to_builtins(pricing),
        )
        await daemon.resources.repository.update(
            ActorORM,
            resources.actor.id,
            budget=msgspec.to_builtins(YuuAgentBudget(max_usd=1.0)),
        )
        await daemon.resources.event_bus.drain()

        binding = await load_actor_binding(
            daemon.resources.repository,
            resources.actor.id,
            workspace_path=tmp_path / "ws",
        )
        # Should not raise
        actor = start_yuuagents_actor(binding, yuuagents_config=yuubot_config.yuuagents)
        await actor.close()
    finally:
        await daemon.stop()


async def test_provider_cost_takes_precedence_over_pricing(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider(provider_cost=0.25)
    monkeypatch.setitem(yuuagents_stage._PROVIDER_CLASSES, "openai", lambda **_: llm)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    store = yuutrace.init_memory()
    try:
        resources = await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
        )
        pricing = PricingTable(
            entries=(PricingEntry(model="gpt-4", input_per_million=1.0),)
        )
        await daemon.resources.repository.update(
            LLMBackendORM,
            resources.llm_backend.id,
            pricing=msgspec.to_builtins(pricing),
        )
        await daemon.resources.event_bus.drain()

        async with _client(daemon) as client:
            response = await client.post(
                "/integration/echo",
                json={
                    "integration_id": resources.integration.id,
                    "message_id": MESSAGE_ID,
                    "sender_id": SENDER_ID,
                    "sender_name": "Trace User",
                    "kind": "private",
                    "text": ORIGINAL_TEXT,
                    "segments": [{"kind": "text", "text": ORIGINAL_TEXT}],
                },
            )
        assert response.status_code == 202, response.json()

        actor = daemon.actors.running_actor(resources.actor.id)
        assert isinstance(actor, SimpleLoopActor)
        await actor.next_turn_result()

        conv_id = store.list_conversations()["conversations"][0]["id"]
        conv = store.get_conversation(conv_id)
        assert conv is not None
        assert_cost_event(conv["spans"][-1], category="llm", amount=0.25)
    finally:
        await daemon.stop()


async def test_integration_charge_usage_recorded_in_trace() -> None:
    store = yuutrace.init_memory()
    observer = TraceObserver()
    eventbus = EventBus()
    eventbus.subscribe(observer)
    observer.register(
        "trace-agent",
        conversation_id="00000000-0000-0000-0000-000000000001",
        character_name="trace-character",
        model="gpt-4",
    )
    task_id = UUID("00000000-0000-0000-0000-000000000002")
    sink = UsageSink(
        eventbus=eventbus,
        task_id=task_id,
        budget=Budget(limits={}),
        attributes={"agent_name": "trace-agent", "agent_id": "trace-agent-id"},
    )
    context = InvocationContext(
        actor_id=ACTOR_ID,
        integration_id=INTEGRATION_ID,
        capability_id="echo",
        usage=sink,
    )

    conv = yuutrace.conversation(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        agent="trace-agent",
        model="gpt-4",
    )
    with conv:
        turn = conv.start_turn("assistant")
        with turn:
            context.charge_usage("echo-api", 3, "request")
            await asyncio.sleep(0)

    conv_record = store.get_conversation("00000000-0000-0000-0000-000000000001")
    assert conv_record is not None
    turn_span = conv_record["spans"][-1]
    events = assert_tool_usage(turn_span, tool_name="echo-api", call_id=str(task_id))
    attrs = events[0]["attributes"]
    assert attrs["yuubot.actor_id"] == ACTOR_ID
    assert attrs["yuubot.integration_id"] == INTEGRATION_ID
    assert attrs["yuubot.capability_id"] == "echo"


class _UsageProvider:
    """Scripted LLM that returns usage on every call."""

    def __init__(self, *, provider_cost: float | None = None) -> None:
        self.provider_cost = provider_cost

    @property
    def api_type(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "scripted"

    async def list_models(self) -> list[yuullm.ProviderModel]:
        return [yuullm.ProviderModel(id="gpt-4")]

    async def stream(
        self,
        messages: list[yuullm.Message],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        async def _items() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": "done"})

        return _items(), yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model="gpt-4",
                input_tokens=10,
                output_tokens=5,
            ),
            provider_cost=self.provider_cost,
        )


async def _build_daemon(base_config: BootstrapConfig, tmp_path: Path) -> YuubotDaemon:
    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(
                data_dir=str(tmp_path / "data"),
            ),
        ),
    )


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://testserver",
    )
