"""E2E coverage for trace/cost backend wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest
import yuullm
import yuutrace
import yuuagents.stage as yuuagents_stage

from helpers import (
    insert_echo_actor_resources,
)
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.actors import SimpleLoopActor
from yuubot.core.assembly import start_yuuagents_actor
from yuubot.core.bindings import load_actor_binding
from yuubot.core.validation import ConfigurationError
from yuubot.resources.records import (
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


async def test_llm_usage_recorded_in_trace(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = yuutrace.init_memory()

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
        with pytest.raises(ConfigurationError, match="max_usd budget requires pricing"):
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


class _UsageProvider:
    """Scripted LLM that returns usage on every call."""

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
            )
        )


async def _build_daemon(base_config: BootstrapConfig, tmp_path: Path) -> YuubotDaemon:
    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(
                data_dir=str(tmp_path / "data"),
                workspace_dir=str(tmp_path / "workspaces"),
                logs_dir=str(tmp_path / "logs"),
            ),
        ),
    )


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://testserver",
    )
