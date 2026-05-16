"""Daemon-level completion smoke tests for the architecture-v2 runtime."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest
import yuullm
import yuuagents.stage as yuuagents_stage

from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.actors import SimpleLoopActor
from yuubot.core.integrations.echo import (
    ECHO_CAPABILITY_ID,
    ECHO_INTEGRATION_NAME,
    EchoIntegration,
    EchoPayload,
)
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    BudgetPolicy,
    CharacterHints,
    CharacterRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
    ResourcePolicy,
    RuntimePolicy,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.runtime.daemon import YuubotDaemon, build_daemon


SOURCE_PATH = "dialogs/web-chat"
DAEMON_HEADERS = {"X-Daemon-Secret": "test-daemon-secret"}


async def test_daemon_completion_smoke_runs_real_daemon_turn_and_refreshes(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = ScriptedProvider(_simple_loop_turns())
    monkeypatch.setitem(yuuagents_stage._PROVIDER_CLASSES, "openai", lambda **_: llm)
    daemon = await _build_daemon(yuubot_config, tmp_path)

    await daemon.start()
    try:
        async with _client(daemon) as client:
            integration, actor = await _provision_smoke_resources(
                client,
                actor_id="actor-main",
                source_path=SOURCE_PATH,
            )
        instance = _echo_instance(daemon, integration.id)

        await instance.send_to_channel(
            message_id="msg-1",
            sender_id="user-1",
            sender_name="Tester",
            kind="private",
            text="hello daemon",
            content=[{"type": "text", "text": "hello daemon"}],
        )

        actor_workspace = daemon.actors.running_actor_workspace_paths()[actor.id]
        assert await instance.next_echo_call() == EchoPayload(
            message="hello from python",
            value=actor_workspace,
            sender_id="user-1",
            message_id="msg-1",
        )
        context = await instance.next_echo_context()
        assert context["actor_id"] == actor.id
        assert context["raw"] == {}

        turn = await _next_simple_loop_turn(daemon, actor.id)
        assert turn.message_id == "msg-1"
        assert turn.assistant_text == "done"
        assert len(llm.calls) == 2
        assert "hello daemon" in yuullm.render_message_text(llm.calls[0][-1])

        async with _client(daemon) as client:
            status = await client.get(
                "/api/status",
                headers=DAEMON_HEADERS,
            )

        assert status.status_code == 200
        assert status.json() == {
            "status": "running",
            "running_integration_ids": [integration.id],
            "running_actor_ids": [actor.id],
            "actor_workspaces": {actor.id: actor_workspace},
            "route_binding_count": 2,
            "trace": {"enabled": False, "status": "disabled"},
        }

        await _assert_refresh_cases(daemon, SOURCE_PATH, actor.id)
    finally:
        await daemon.stop()


@dataclass
class ScriptedProvider:
    turns: list[list[yuullm.StreamItem]]
    calls: list[list[yuullm.Message]]
    tools: list[list[dict[str, Any]]]

    def __init__(self, turns: list[list[yuullm.StreamItem]]) -> None:
        self.turns = [list(turn) for turn in turns]
        self.calls = []
        self.tools = []

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
        _ = model, on_raw_chunk, kwargs
        self.calls.append(list(messages))
        self.tools.append(list(tools or ()))
        turn = self.turns.pop(0)

        async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
            for item in turn:
                yield item

        store = yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model="fake",
                input_tokens=1,
                output_tokens=1,
            )
        )
        return stream_items(), store


async def _build_daemon(
    base_config: BootstrapConfig,
    tmp_path: Path,
) -> YuubotDaemon:
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


async def _provision_smoke_resources(
    client: httpx.AsyncClient,
    *,
    actor_id: str,
    source_path: str,
) -> tuple[IntegrationRecord, ActorRecord]:
    await _post_character(client, actor_id)
    await _post_llm_backend(client, actor_id)
    integration = await _post_integration(client, source_path)
    actor = await _post_actor(client, actor_id)
    await _post_actor_ingress_rule(client, integration.id, source_path, actor.id)
    return integration, actor


async def _assert_refresh_cases(
    daemon: YuubotDaemon,
    source_path: str,
    actor_id: str,
) -> None:
    async with _client(daemon) as client:
        await _post_character(client, "actor-secondary")
        await _post_llm_backend(client, "actor-secondary")
        second_actor = await _post_actor(client, "actor-secondary")
        second_rule = await _post_actor_ingress_rule(
            client,
            "echo-main",
            source_path,
            second_actor.id,
        )
    assert daemon.actors.running_actor_ids() == [actor_id, second_actor.id]

    async with _client(daemon) as client:
        disabled_rule = await client.request(
            "PUT",
            f"/api/resources/ingress-rules/{second_rule.id}",
            headers=DAEMON_HEADERS,
            json={"enabled": False},
        )
    assert disabled_rule.status_code == 200, disabled_rule.json()
    assert disabled_rule.json()["actions"] == ["routes.reloaded", "actors.reconciled"]
    assert disabled_rule.json()["data"]["enabled"] is False
    async with _client(daemon) as client:
        list_rules = await client.get(
            "/api/resources/ingress-rules",
            headers=DAEMON_HEADERS,
        )
    assert [
        rule["enabled"]
        for rule in list_rules.json()["data"]
        if rule["actor_id"] == second_actor.id
    ] == [False]
    assert daemon.gateway.routes.actor_ids() == [actor_id]
    assert daemon.actors.running_actor_ids() == [actor_id]

    async with _client(daemon) as client:
        disabled_actor = await client.post(
            f"/api/resources/actors/{actor_id}/disable",
            headers=DAEMON_HEADERS,
        )
    assert disabled_actor.status_code == 200
    assert daemon.actors.running_actor_ids() == []

    async with _client(daemon) as client:
        enabled_actor = await client.post(
            f"/api/resources/actors/{actor_id}/enable",
            headers=DAEMON_HEADERS,
        )
    assert enabled_actor.status_code == 200
    assert daemon.actors.running_actor_ids() == [actor_id]

    async with _client(daemon) as client:
        disabled_integration = await client.post(
            "/api/resources/integrations/echo-main/disable",
            headers=DAEMON_HEADERS,
        )
    assert disabled_integration.status_code == 200
    assert daemon.integrations.running_integration_ids() == []
    with pytest.raises(LookupError):
        await _invoke_echo(daemon, actor_id, "blocked")

    async with _client(daemon) as client:
        enabled_integration = await client.post(
            "/api/resources/integrations/echo-main/enable",
            headers=DAEMON_HEADERS,
        )
    assert enabled_integration.status_code == 200
    assert daemon.integrations.running_integration_ids() == ["echo-main"]
    assert await _invoke_echo(daemon, actor_id, "restored") == EchoPayload(
        value="restored"
    )


async def _post_character(client: httpx.AsyncClient, actor_id: str) -> CharacterRecord:
    response = await client.post(
        "/api/resources/characters",
        headers=DAEMON_HEADERS,
        json=_record_payload(_character_record(actor_id)),
    )
    return _created(response, CharacterRecord)


async def _post_llm_backend(client: httpx.AsyncClient, actor_id: str) -> LLMBackendRecord:
    response = await client.post(
        "/api/resources/llm-backends",
        headers=DAEMON_HEADERS,
        json=_record_payload(_llm_backend_record(actor_id)),
    )
    return _created(response, LLMBackendRecord)


async def _post_integration(
    client: httpx.AsyncClient,
    source_path: str,
) -> IntegrationRecord:
    response = await client.post(
        "/api/resources/integrations",
        headers=DAEMON_HEADERS,
        json=_record_payload(_integration_record(source_path)),
    )
    return _created(response, IntegrationRecord)


async def _post_actor(client: httpx.AsyncClient, actor_id: str) -> ActorRecord:
    response = await client.post(
        "/api/resources/actors",
        headers=DAEMON_HEADERS,
        json=_actor_payload(actor_id),
    )
    return _created(response, ActorRecord)


async def _post_actor_ingress_rule(
    client: httpx.AsyncClient,
    source_id_pattern: str,
    source_path_pattern: str,
    actor_id: str,
) -> ActorIngressRuleRecord:
    response = await client.post(
        "/api/resources/ingress-rules",
        headers=DAEMON_HEADERS,
        json={
            "actor_id": actor_id,
            "source_id_pattern": source_id_pattern,
            "source_path_pattern": source_path_pattern,
        },
    )
    return _created(response, ActorIngressRuleRecord)


def _created(response: httpx.Response, record_type: type[Any]) -> Any:
    body = response.json()
    assert response.status_code == 201, body
    assert body["status"] == "ok", body
    return msgspec.convert(body["data"], type=record_type, strict=False)


def _character_record(actor_id: str) -> CharacterRecord:
    return CharacterRecord(
        id=f"{actor_id}-char",
        name=f"{actor_id}-char",
        description="",
        system_prompt="You are a smoke-test actor.",
        default_prompt_providers=(),
        facade_module="yuubot.core.facade",
        default_hints=CharacterHints(),
    )


def _llm_backend_record(actor_id: str) -> LLMBackendRecord:
    return LLMBackendRecord(
        id=f"{actor_id}-backend",
        name=f"{actor_id}-backend",
        yuuagents_provider="openai",
        default_model="gpt-4",
        model_capabilities=ModelCapabilities(tool_calling=True),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
    )


def _integration_record(source_path: str) -> IntegrationRecord:
    return IntegrationRecord(
        id="echo-main",
        name=ECHO_INTEGRATION_NAME,
        config={"source_path": source_path},
    )


def _actor_payload(actor_id: str) -> dict[str, object]:
    return _record_payload(_actor_record(actor_id))


def _record_payload(record: object) -> dict[str, object]:
    return msgspec.json.decode(msgspec.json.encode(record))


def _actor_record(actor_id: str) -> ActorRecord:
    character = _character_record(actor_id)
    backend = _llm_backend_record(actor_id)
    return ActorRecord(
        id=actor_id,
        name=actor_id,
        character=character,
        llm_backend=backend,
        model="",
        llm_options=YuuAgentLLMOptions(),
        budget=YuuAgentBudget(max_steps=4),
        agent_capabilities=(),
        agent_prompt_providers=(),
        allowed_capability_ids=(ECHO_CAPABILITY_ID,),
        runtime_policy=RuntimePolicy(),
        resource_policy=ResourcePolicy(workspace_access="read_write"),
    )


def _simple_loop_turns() -> list[list[yuullm.StreamItem]]:
    code = (
        "import os\n"
        "import yext\n"
        "result = await yext.echo.echo(\n"
        "    value=os.getcwd(),\n"
        "    message='hello from python',\n"
        "    sender_id='user-1',\n"
        "    message_id='msg-1',\n"
        ")\n"
        "print(result)"
    )
    return [
        [
            yuullm.ToolCall(
                id="call-python",
                name="execute_python",
                arguments=json.dumps(
                    {
                        "code": code,
                        "timeout_s": 10,
                        "capture": ["stdout", "stderr"],
                    }
                ),
            )
        ],
        [yuullm.Response({"type": "text", "text": "done"})],
    ]


async def _invoke_echo(
    daemon: YuubotDaemon,
    actor_id: str,
    value: str,
) -> EchoPayload:
    result = await daemon.integrations.invoke(
        actor_id=actor_id,
        capability_id=ECHO_CAPABILITY_ID,
        payload={"value": value},
    )
    assert isinstance(result, EchoPayload)
    return result


def _echo_instance(daemon: YuubotDaemon, integration_id: str) -> EchoIntegration:
    instance = daemon.integrations.running_instance(integration_id)
    assert isinstance(instance, EchoIntegration)
    return instance


async def _next_simple_loop_turn(daemon: YuubotDaemon, actor_id: str):
    actor = daemon.actors.running_actor(actor_id)
    assert isinstance(actor, SimpleLoopActor)
    return await actor.next_turn_result()


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://testserver",
    )
