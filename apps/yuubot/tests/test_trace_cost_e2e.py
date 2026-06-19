"""E2E coverage for trace/cost backend wiring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
import msgspec
import pytest
import yuullm
import yuutrace
from yuuagents import YuuTraceObserver, EventBus
from yuuagents.types.values import EventPayload

from tests.helpers import (
    assert_cost_event,
    assert_tool_usage,
    insert_echo_actor_resources,
    register_test_llm_provider,
    make_test_daemon_infrastructure,
)
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.actors import SimpleLoopActor
from yuubot.core.assembly._stage import _check_pricing_for_budget
from yuubot.core.bindings import load_actor_binding
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.observability import YuubotTraceContextProvider
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
    register_test_llm_provider("openai", llm)

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

        await daemon.actors.start_actor(resources.actor.id)

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
        await _wait_for_trace_conversations(store)

        result = store.list_conversations()
        assert result["total"] >= 1, "expected at least one conversation trace"

        conv_id = result["conversations"][0]["id"]
        conv = store.get_conversation(conv_id)
        assert conv is not None

        all_event_names = [
            ev["name"] for span in conv["spans"] for ev in span["events"]
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
    register_test_llm_provider("openai", llm)

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
            default_budget=msgspec.to_builtins(YuuAgentBudget(max_usd=1.0)),
        )
        await daemon.resources.event_bus.drain()

        binding = await load_actor_binding(
            daemon.resources.repository,
            resources.actor.id,
            workspace_path=tmp_path / "ws",
        )
        with pytest.raises(ConfigurationError, match="USD budget requires pricing"):
            _check_pricing_for_budget(binding.default_agent_binding())
    finally:
        await daemon.stop()


async def test_pricing_check_raises_when_backend_budget_set_without_pricing(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider()
    register_test_llm_provider("openai", llm)

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
            _check_pricing_for_budget(binding.default_agent_binding())
    finally:
        await daemon.stop()


async def test_pricing_check_passes_when_pricing_entry_exists(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider()
    register_test_llm_provider("openai", llm)

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
        pricing = PricingTable(
            entries=(
                PricingEntry(
                    model="gpt-4", input_per_million=1.0, output_per_million=2.0
                ),
            )
        )
        await daemon.resources.repository.update(
            LLMBackendORM,
            resources.llm_backend.id,
            pricing=msgspec.to_builtins(pricing),
        )
        await daemon.resources.repository.update(
            ActorORM,
            resources.actor.id,
            default_budget=msgspec.to_builtins(YuuAgentBudget(max_usd=1.0)),
        )
        await daemon.resources.event_bus.drain()

        binding = await load_actor_binding(
            daemon.resources.repository,
            resources.actor.id,
            workspace_path=tmp_path / "ws",
        )
        # Should not raise — pricing entry exists for the budgeted model
        _check_pricing_for_budget(binding.default_agent_binding())
    finally:
        await daemon.stop()


async def test_provider_cost_takes_precedence_over_pricing(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _UsageProvider(provider_cost=0.25)
    register_test_llm_provider("openai", llm)

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

        await daemon.actors.start_actor(resources.actor.id)

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
        await _wait_for_trace_conversations(store)

        conv_id = store.list_conversations()["conversations"][0]["id"]
        conv = store.get_conversation(conv_id)
        assert conv is not None
        assert_cost_event(conv["spans"][-1], category="llm", amount=0.25)
    finally:
        await daemon.stop()


async def test_integration_charge_usage_recorded_in_trace() -> None:
    store = yuutrace.init_memory()
    trace_context = YuubotTraceContextProvider()
    observer = YuuTraceObserver(context_provider=trace_context)
    eventbus = EventBus()
    eventbus.subscribe(observer)
    trace_context.register(
        "trace-agent",
        character_name="trace-character",
        model="gpt-4",
    )
    task_id = UUID("00000000-0000-0000-0000-000000000002")
    recorder = _TraceUsageRecorder(
        eventbus=eventbus,
        task_id=task_id,
        attributes={"agent_name": "trace-agent", "agent_id": "trace-agent-id"},
    )

    # agent.started creates the conversation via EventBus
    await eventbus.emit(
        "agent.started",
        {
            "agent_id": "trace-agent-id",
            "agent_name": "trace-agent",
            "history": [],
        },
    )
    await asyncio.sleep(0)

    async with eventbus.scope(
        "agent.turn",
        {
            "agent_id": "trace-agent-id",
            "agent_name": "trace-agent",
        },
    ):
        context = InvocationContext(
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            capability_id="echo",
            usage=recorder,
        )
        context.charge_usage("echo-api", 3, "request")
        await asyncio.sleep(0)
    await asyncio.sleep(0)

    conversations = store.list_conversations()
    assert conversations["total"] >= 1, "expected at least one conversation"
    conv_id = conversations["conversations"][0]["id"]
    conv_record = store.get_conversation(conv_id)
    assert conv_record is not None

    all_event_names = [
        ev["name"] for span in conv_record["spans"] for ev in span["events"]
    ]
    assert "yuu.tool.usage" in all_event_names, (
        f"expected yuu.tool.usage event in trace, got: {all_event_names}"
    )
    turn_span = conv_record["spans"][-1]
    events = assert_tool_usage(turn_span, tool_name="echo-api", call_id=str(task_id))
    attrs = events[0]["attributes"]
    assert attrs["yuubot.actor_id"] == ACTOR_ID
    assert attrs["yuubot.integration_id"] == INTEGRATION_ID
    assert attrs["yuubot.capability_id"] == "echo"


class _TraceUsageRecorder:
    def __init__(
        self,
        *,
        eventbus: EventBus,
        task_id: UUID,
        attributes: Mapping[str, object],
    ) -> None:
        self._eventbus = eventbus
        self._task_id = task_id
        self._attributes = dict(attributes)

    def charge(
        self,
        service: str,
        amount: float,
        unit: str,
        *,
        category: str | None = None,
        metadata: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        del category, metadata
        payload = {
            **self._attributes,
            **(attributes or {}),
            "service": service,
            "amount": amount,
            "unit": unit,
            "task_id": str(self._task_id),
        }
        asyncio.create_task(
            self._eventbus.emit("runtime.usage_reported", cast(EventPayload, payload))
        )


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
        history: yuullm.History,
        *,
        model: str,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        _ = history, model, on_raw_chunk, kwargs

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
        components=make_test_daemon_infrastructure(),
    )


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://testserver",
    )


async def _wait_for_trace_conversations(
    store,
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while store.list_conversations()["total"] < 1:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("expected at least one trace conversation")
        await asyncio.sleep(0.01)
