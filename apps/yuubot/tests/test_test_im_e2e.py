"""E2E tests for the test_im integration — ingress, multi-channel routing, SDK path."""

from __future__ import annotations

import asyncio
from pathlib import Path
import httpx
import msgspec
import pytest
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.core.facade import IntegrationInvokeBridge
from yuubot.core.facade.protocol import FacadeRpcRequest, ImSendPayload
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
from yuubot.core.routing import load_route_bindings
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    BudgetPolicy,
    CapabilitySetRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelConfig,
    Pricing,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CapabilitySetORM,
    IntegrationORM,
    LLMBackendORM,
)
from tests.helpers import (
    make_test_daemon_infrastructure,
    register_test_llm_provider,
)
from tests.llm_prompt.framework import PromptCapture
from tests.llm_prompt.scenario import ToolCall

# Local import to avoid pytest fixture scanning of msgspec Struct classes
import yuubot.core.integrations.impls.test_im.integration as _tim_mod


async def test_single_channel_ingress_and_response(resources: Resources, tmp_path: Path):
    """Calling response(path=...) puts the message in that channel's
    outbound queue, and only that channel."""
    repository = resources.repository
    factory = _tim_mod.TestImIntegrationFactory()
    channel = await _create_test_im_integration(
        repository,
        "test-im-1",
        channels=[
            _tim_mod.TestImConfigChannel(id="group-1", name="General", source_path="channels/group-1"),
            _tim_mod.TestImConfigChannel(id="group-2", name="OffTopic", source_path="channels/group-2"),
        ],
    )
    actor = await _create_actor_bundle(repository, "tim-actor")
    await _create_actor_ingress_rule(repository, channel.id, "channels/group-1", actor.id)

    gateway = Gateway(routes=await load_route_bindings(repository))
    factories = IntegrationFactoryRegistry()
    factories.register(factory)
    integrations = IntegrationCore(
        repository=repository,
        factories=factories,
        gateway=gateway,
        integrations_root=tmp_path / "data" / "integrations",
    )
    await integrations.refresh_capabilities()
    await integrations.enable(channel.id)

    try:
        instance = factory.instance(channel.id)

        msg = await instance.send_to_channel(
            "group-1", sender_id="user-1", sender_name="Alice", text="hello",
        )
        assert msg.source.path == "channels/group-1"

        await instance.response(target_msg_id=msg.message_id, path="group-1", msg="Hello back!")

        outbound = await instance.next_outbound("group-1")
        assert outbound["path"] == "group-1"
        assert outbound["msg"] == "Hello back!"
        assert outbound["target_msg_id"] == msg.message_id

        # No cross-talk to group-2
        with pytest.raises(asyncio.TimeoutError):
            await instance.next_outbound("group-2", timeout=0.1)

        # Unknown path silently dropped; next_outbound for that path raises
        await instance.response(target_msg_id="", path="nonexistent", msg="ghost")
        with pytest.raises(LookupError):
            await instance.next_outbound("nonexistent", timeout=0.1)
    finally:
        await integrations.disable_all()


async def test_multi_channel_ingress_routes_to_correct_actor(resources: Resources, tmp_path: Path):
    """Messages to group-1 reach actor-a's mailbox; messages to group-2
    reach actor-b's mailbox.  Each sees the correct source path."""
    repository = resources.repository
    factory = _tim_mod.TestImIntegrationFactory()
    channel = await _create_test_im_integration(
        repository,
        "test-im-2",
        channels=[
            _tim_mod.TestImConfigChannel(id="group-1", name="General", source_path="channels/group-1"),
            _tim_mod.TestImConfigChannel(id="group-2", name="OffTopic", source_path="channels/group-2"),
        ],
    )
    actor_a = await _create_actor_bundle(repository, "actor-a")
    actor_b = await _create_actor_bundle(repository, "actor-b")
    await _create_actor_ingress_rule(repository, channel.id, "channels/group-1", actor_a.id)
    await _create_actor_ingress_rule(repository, channel.id, "channels/group-2", actor_b.id)

    gateway = Gateway(routes=await load_route_bindings(repository))
    factories = IntegrationFactoryRegistry()
    factories.register(factory)
    integrations = IntegrationCore(
        repository=repository,
        factories=factories,
        gateway=gateway,
        integrations_root=tmp_path / "data" / "integrations",
    )
    await integrations.refresh_capabilities()
    await integrations.enable(channel.id)

    try:
        instance = factory.instance(channel.id)
        mailbox_a = gateway.get_mailbox(actor_a.id)
        mailbox_b = gateway.get_mailbox(actor_b.id)

        msg_1 = await instance.send_to_channel("group-1", sender_id="u1", text="group-1 msg")
        recv_a = await asyncio.wait_for(mailbox_a.recv(), timeout=2.0)
        assert recv_a.message_id == msg_1.message_id
        assert recv_a.source.path == "channels/group-1"
        assert mailbox_b.empty()

        msg_2 = await instance.send_to_channel("group-2", sender_id="u2", text="group-2 msg")
        recv_b = await asyncio.wait_for(mailbox_b.recv(), timeout=2.0)
        assert recv_b.message_id == msg_2.message_id
        assert recv_b.source.path == "channels/group-2"

        prefix = msg_1.render_metadata()
        assert "source=" in prefix
        assert "path=channels/group-1" in prefix
        assert "u1" in prefix
    finally:
        await integrations.disable_all()


# ---------------------------------------------------------------------------
# Test 4: full daemon E2E — tim.Channel.send() via compiled LLM
# ---------------------------------------------------------------------------


def _tim_channel_send_llm() -> PromptCapture:
    """Factory: returns a compiled PromptCapture that scripts the LLM to
    call execute_python → tim.Channel("group-1").send("Hello from bot!")."""
    capture = PromptCapture()
    capture.set_response_script([
        ToolCall("execute_python", {
            "code": (
                "import tim\n"
                "channel = tim.Channel('group-1')\n"
                "await channel.send('Hello from bot!')\n"
            ),
        }),
        None,
    ])
    return capture


async def test_tim_channel_send_e2e(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Full-stack E2E: LLM sees source context, calls tim.Channel.send(),
    and the message arrives at the integration's outbound queue."""
    llm = _tim_channel_send_llm()
    register_test_llm_provider("openai", llm)

    daemon = await _build_test_im_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        repo = daemon.resources.repository

        # echo integration for capabilities
        from tests.helpers import insert_echo_actor_resources
        await insert_echo_actor_resources(
            repo,
            actor_id="e2e-actor",
            integration_id="e2e-echo",
            source_path="channels/echo",
            system_prompt="You reply with tim.Channel.send().",
        )

        # test_im integration with one channel
        await repo.insert(
            IntegrationORM,
            IntegrationRecord(
                id="e2e-testim",
                name="test_im",
                config=msgspec.to_builtins(_tim_mod.TestImConfig(
                    channels=[_tim_mod.TestImConfigChannel(
                        id="group-1", name="General", source_path="channels/group-1",
                    )],
                )),
            ),
        )
        await daemon.resources.event_bus.drain()

        # ingress rule
        await repo.insert(
            ActorIngressRuleORM,
            ActorIngressRuleRecord(
                id="e2e-testim:channels/group-1:e2e-actor",
                actor_id="e2e-actor",
                source_id_pattern="e2e-testim",
                source_path_pattern="channels/group-1",
            ),
        )
        await daemon.resources.event_bus.drain()
        await daemon.actors.start_actor("e2e-actor")

        # Send message via test_im HTTP
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=daemon.asgi_app()),
            base_url="http://testserver",
        ) as client:
            await client.post(
                "/integration/test_im",
                json={
                    "integration_id": "e2e-testim",
                    "channel_id": "group-1",
                    "sender_id": "user-1",
                    "sender_name": "Alice",
                    "text": "hello from alice",
                    "content": [{"type": "text", "text": "hello from alice"}],
                },
            )

        # Wait for all LLM calls (execute_python tool + terminal)
        await llm.wait_for_calls(2)

        # Assert on prompt snapshot
        snapshot = llm.snapshot(0)
        assert "path=channels/group-1" in snapshot.user_text, (
            "User message should contain source path"
        )
        assert "Alice" in snapshot.user_text, (
            "User message should contain sender name"
        )

        # Assert on side effect: integration received the message
        instance = daemon.integrations.running_instance("e2e-testim")
        outbound = await instance.next_outbound("group-1", timeout=3.0)
        assert outbound["msg"] == "Hello from bot!"
        assert outbound["path"] == "group-1"
    finally:
        await daemon.stop()


async def _build_test_im_daemon(
    base_config: BootstrapConfig,
    tmp_path: Path,
):
    return await _build_daemon(base_config, tmp_path)


async def test_bridge_im_send_dispatches_directly_to_integration(resources: Resources, tmp_path: Path):
    """The bridge's im_send handler routes messages to the integration
    without going through the actor mailbox."""
    repository = resources.repository
    factory = _tim_mod.TestImIntegrationFactory()
    channel = await _create_test_im_integration(
        repository,
        "test-im-3",
        channels=[
            _tim_mod.TestImConfigChannel(id="group-1", name="General", source_path="channels/group-1"),
        ],
    )

    gateway = Gateway(routes=await load_route_bindings(repository))
    factories = IntegrationFactoryRegistry()
    factories.register(factory)
    integrations = IntegrationCore(
        repository=repository,
        factories=factories,
        gateway=gateway,
        integrations_root=tmp_path / "data" / "integrations",
    )
    await integrations.refresh_capabilities()
    await integrations.enable(channel.id)

    bridge = IntegrationInvokeBridge(integrations=integrations)
    await bridge.start()
    try:
        instance = factory.instance(channel.id)

        payload = ImSendPayload(path="group-1", text="Hello via bridge!")
        request = FacadeRpcRequest(
            token=bridge.endpoint.token,
            kind="im_send",
            actor_id="test-actor",
            agent_name="test-agent",
            session_id="test-session",
            mailbox_id="test-mailbox",
            payload=msgspec.to_builtins(payload),
        )

        response = await bridge._dispatch(msgspec.json.encode(request))
        assert response.ok, f"im_send failed: {response.error}"

        outbound = await instance.next_outbound("group-1")
        assert outbound["msg"] == "Hello via bridge!"
    finally:
        await bridge.stop()
        await integrations.disable_all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_daemon(
    base_config: BootstrapConfig,
    tmp_path: Path,
):
    from yuubot.runtime.daemon import build_daemon

    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(data_dir=str(tmp_path / "data")),
        ),
        components=make_test_daemon_infrastructure(),
    )


async def _create_test_im_integration(
    repository: ResourceRepository,
    integration_id: str,
    channels: list,
) -> IntegrationRecord:
    return await repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id=integration_id,
            name="test_im",
            config=msgspec.to_builtins(_tim_mod.TestImConfig(channels=channels)),
        ),
    )


async def _create_actor_ingress_rule(
    repository: ResourceRepository,
    source_id_pattern: str,
    source_path_pattern: str,
    actor_id: str,
) -> ActorIngressRuleRecord:
    return await repository.insert(
        ActorIngressRuleORM,
        ActorIngressRuleRecord(
            id=f"{source_id_pattern}:{source_path_pattern}:{actor_id}",
            actor_id=actor_id,
            source_id_pattern=source_id_pattern,
            source_path_pattern=source_path_pattern,
        ),
    )


async def _create_actor_bundle(repository: ResourceRepository, actor_id: str) -> ActorRecord:
    backend = await _create_llm_backend(repository, f"{actor_id}-backend")
    capability_set = await repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(id=f"{actor_id}-capabilities", name=f"{actor_id}-capabilities"),
    )
    return await repository.insert(
        ActorORM,
        ActorRecord(
            id=actor_id,
            name=actor_id,
            type="simple_loop",
            persona_prompt="You are a test actor.",
            capability_set_id=capability_set.id,
            llm_backend_id=backend.id,
            model="",
        ),
    )


async def _create_llm_backend(repository: ResourceRepository, backend_id: str) -> LLMBackendRecord:
    return await repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id=backend_id,
            name=backend_id,
            provider_identity="openai",
            recommended_model="gpt-4",
            model_configs={
                "gpt-4": ModelConfig(
                    pricing=Pricing(),
                    capabilities=ModelCapabilities(tool_calling=True),
                )
            },
            budget=BudgetPolicy(),
        ),
    )
