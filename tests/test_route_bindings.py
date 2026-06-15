"""Gateway route bindings from actor ingress rule rows."""

from __future__ import annotations

import asyncio

import pytest

from yuubot.core.gateway import Gateway
from yuubot.core.messages import IncomingMessage, MessageSource, system_source_for_actor
from yuubot.core.routing import (
    ActorIngressRule,
    RouteBindings,
    build_route_bindings,
    load_route_bindings,
)
from yuubot.events import Event
from yuubot.resources.events import ResourceChanged
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    BudgetPolicy,
    CharacterHints,
    CharacterRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
    ResourcePolicy,
    RuntimePolicy,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.root import Resources
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CharacterORM,
    LLMBackendORM,
)


class TestRouteBindings:
    async def test_create_actor_ingress_rule(self, resources: Resources):
        repository = resources.repository
        actor = await _create_actor_bundle(repository, "test-actor")
        rule = await _create_rule(repository, "slack-main", "channels/dev", actor.id)

        rules = await repository.list(ActorIngressRuleORM)

        assert rule.source_id_pattern == "slack-main"
        assert rule.source_path_pattern == "channels/dev"
        assert rule.actor_id == actor.id
        assert _rules_for_actor(rules, actor.id) == (rule,)

    async def test_delete_actor_ingress_rule(self, resources: Resources):
        repository = resources.repository
        actor = await _create_actor_bundle(repository, "test-actor")
        rule = await _create_rule(repository, "slack-main", "channels/dev", actor.id)

        deleted = await repository.delete(ActorIngressRuleORM, rule.id)
        rules = await repository.list(ActorIngressRuleORM)

        assert deleted is True
        assert _rules_for_actor(rules, actor.id) == ()

    async def test_build_route_bindings_from_rows(self, resources: Resources):
        repository = resources.repository
        actor1 = await _create_actor_bundle(repository, "actor-1")
        actor2 = await _create_actor_bundle(repository, "actor-2")
        await _create_rule(repository, "slack-main", "channels/dev", actor1.id)
        await _create_rule(repository, "github-main", "repos/yuulabs/*", actor2.id)

        bindings = await load_route_bindings(repository)

        assert bindings.resolve(_msg("slack-main", "channels/dev")) == [actor1.id]
        assert bindings.resolve(_msg("github-main", "repos/yuulabs/yuubot-v2")) == [
            actor2.id,
        ]

    async def test_build_route_bindings_allows_multiple_targets(
        self, resources: Resources
    ):
        repository = resources.repository
        actor1 = await _create_actor_bundle(repository, "actor-1")
        actor2 = await _create_actor_bundle(repository, "actor-2")
        await _create_rule(repository, "slack-main", "channels/support", actor1.id)
        await _create_rule(repository, "slack-main", "channels/support", actor2.id)

        bindings = await load_route_bindings(repository)

        assert bindings.resolve(_msg("slack-main", "channels/support")) == [
            actor1.id,
            actor2.id,
        ]

    async def test_build_route_bindings_uses_source_and_kind_globs(self):
        bindings = build_route_bindings(
            (
                ActorIngressRuleRecord(
                    id="support",
                    actor_id="support-agent",
                    source_id_pattern="slack-*",
                    source_path_pattern="channels/*",
                    kind_patterns=("mention", "dm"),
                ),
            )
        )

        assert bindings.resolve(_msg("slack-main", "channels/support", "mention")) == [
            "support-agent",
        ]
        with pytest.raises(LookupError):
            bindings.resolve(_msg("github-main", "channels/support", "mention"))
        with pytest.raises(LookupError):
            bindings.resolve(_msg("slack-main", "channels/support", "message"))

    async def test_system_source_routes_to_owning_actor(self, resources: Resources):
        repository = resources.repository
        actor = await _create_actor_bundle(repository, "actor-1")

        bindings = await load_route_bindings(repository)

        message = IncomingMessage(
            message_id="system-1",
            sender_id="system",
            source=system_source_for_actor(actor.id, path="schedules/nightly"),
            kind="schedule.tick",
        )
        assert bindings.resolve(message) == [actor.id]

    async def test_build_route_bindings_empty(self):
        bindings = build_route_bindings(())
        with pytest.raises(LookupError):
            bindings.resolve(_msg("missing", "path"))

    async def test_gateway_routes_message_to_mailbox(self, resources: Resources):
        repository = resources.repository
        actor = await _create_actor_bundle(repository, "test-actor")
        await _create_rule(repository, "slack-main", "channels/dev", actor.id)
        gateway = Gateway(routes=await load_route_bindings(repository))
        mailbox = gateway.get_mailbox(actor.id)

        await gateway.ingest(_msg("slack-main", "channels/dev", text="hello"))

        received = await asyncio.wait_for(mailbox.get(), timeout=1.0)
        assert received.content[0]["text"] == "hello"

    async def test_gateway_update_bindings_refreshes_routing(
        self, resources: Resources
    ):
        repository = resources.repository
        char = await _create_character(repository, "test-char")
        backend = await _create_llm_backend(repository, "test-backend")
        actor1 = await _create_actor(repository, "actor-1", char, backend)
        actor2 = await _create_actor(repository, "actor-2", char, backend)
        rule1 = await _create_rule(repository, "slack-main", "channels/dev", actor1.id)
        gateway = Gateway(routes=await load_route_bindings(repository))
        mb1 = gateway.get_mailbox(actor1.id)

        await gateway.ingest(_msg("slack-main", "channels/dev", text="first"))
        assert (await asyncio.wait_for(mb1.get(), timeout=1.0)).content[0][
            "text"
        ] == "first"

        await repository.delete(ActorIngressRuleORM, rule1.id)
        await _create_rule(repository, "slack-main", "channels/dev", actor2.id)
        gateway.update_bindings(await load_route_bindings(repository))
        mb2 = gateway.get_mailbox(actor2.id)

        await gateway.ingest(_msg("slack-main", "channels/dev", text="second"))
        assert (await asyncio.wait_for(mb2.get(), timeout=1.0)).content[0][
            "text"
        ] == "second"

    async def test_actor_reads_latest_llm_backend_details(self, resources: Resources):
        repository = resources.repository
        char = await _create_character(repository, "test-char")
        backend = await _create_llm_backend(repository, "test-backend")
        actor = await _create_actor(repository, "actor-1", char, backend)

        await repository.update(
            LLMBackendORM,
            backend.id,
            default_model="gpt-4.1",
        )

        refreshed = await repository.get(ActorORM, actor.id)

        assert refreshed is not None
        assert refreshed.llm_backend.default_model == "gpt-4.1"

    async def test_gateway_no_mailbox_for_unknown_actor(self):
        gateway = Gateway(
            routes=RouteBindings(
                rules=(
                    ActorIngressRule(
                        actor_id="missing-actor",
                        source_id_pattern="slack-main",
                        source_path_pattern="**",
                        kind_patterns=("*",),
                    ),
                )
            )
        )
        await gateway.ingest(_msg("slack-main", "channels/dev", text="lost"))

    async def test_gateway_unrouted_source_raises(self):
        gateway = Gateway(routes=RouteBindings(rules=()))
        with pytest.raises(LookupError):
            await gateway.ingest(_msg("unknown", "channels/dev", text="lost"))

    async def test_table_change_event_drives_route_reload(self, resources: Resources):
        repository = resources.repository
        actor = await _create_actor_bundle(repository, "test-actor")
        gateway = Gateway(routes=RouteBindings(rules=()))
        reloaded = asyncio.Event()

        async def reload_routes() -> None:
            gateway.update_bindings(await load_route_bindings(repository))
            reloaded.set()

        async def on_changed(event: Event) -> None:
            if isinstance(event, ResourceChanged) and event.is_table(
                "actor_ingress_rules"
            ):
                await reload_routes()

        resources.event_bus.subscribe([ResourceChanged], on_changed)

        with pytest.raises(LookupError):
            gateway.routes.resolve(_msg("slack-main", "channels/dev"))

        rule = await _create_rule(repository, "slack-main", "channels/dev", actor.id)
        await asyncio.wait_for(reloaded.wait(), timeout=1.0)
        assert gateway.routes.resolve(_msg("slack-main", "channels/dev")) == [actor.id]

        reloaded.clear()
        await repository.delete(ActorIngressRuleORM, rule.id)
        await asyncio.wait_for(reloaded.wait(), timeout=1.0)
        with pytest.raises(LookupError):
            gateway.routes.resolve(_msg("slack-main", "channels/dev"))

    async def test_resource_events_broadcast_table_change(self, resources: Resources):
        events: list[ResourceChanged] = []

        async def on_changed(event: Event) -> None:
            if isinstance(event, ResourceChanged):
                events.append(event)

        resources.event_bus.subscribe([ResourceChanged], on_changed)

        char = await _create_character(resources.repository, "event-char")
        await resources.event_bus.drain()

        assert events == [
            ResourceChanged(
                table="characters",
                action="inserted",
                row_ids=(char.id,),
            )
        ]


async def _create_character(
    repository: ResourceRepository,
    name: str,
) -> CharacterRecord:
    record = CharacterRecord(
        id=name,
        name=name,
        description="",
        system_prompt=f"You are {name}",
        facade_module="yuubot.core.facade",
        default_hints=CharacterHints(),
    )
    return await repository.insert(CharacterORM, record)


async def _create_llm_backend(
    repository: ResourceRepository,
    name: str,
) -> LLMBackendRecord:
    record = LLMBackendRecord(
        id=name,
        name=name,
        yuuagents_provider="openai",
        default_model="gpt-4",
        model_capabilities=ModelCapabilities(),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
    )
    return await repository.insert(LLMBackendORM, record)


async def _create_actor(
    repository: ResourceRepository,
    name: str,
    character: CharacterRecord,
    backend: LLMBackendRecord,
) -> ActorRecord:
    record = ActorRecord(
        id=name,
        name=name,
        character=character,
        llm_backend=backend,
        budget=YuuAgentBudget(),
        llm_options=YuuAgentLLMOptions(),
        model="",
        agent_tools=(),
        allowed_capability_ids=(),
        runtime_policy=RuntimePolicy(),
        resource_policy=ResourcePolicy(),
    )
    return await repository.insert(ActorORM, record)


async def _create_rule(
    repository: ResourceRepository,
    source_id_pattern: str,
    source_path_pattern: str,
    actor_id: str,
    *,
    kind_patterns: tuple[str, ...] = ("*",),
) -> ActorIngressRuleRecord:
    record = ActorIngressRuleRecord(
        id=f"{source_id_pattern}:{source_path_pattern}:{actor_id}",
        actor_id=actor_id,
        source_id_pattern=source_id_pattern,
        source_path_pattern=source_path_pattern,
        kind_patterns=kind_patterns,
    )
    return await repository.insert(ActorIngressRuleORM, record)


async def _create_actor_bundle(
    repository: ResourceRepository,
    name: str,
) -> ActorRecord:
    char = await _create_character(repository, f"{name}-char")
    backend = await _create_llm_backend(repository, f"{name}-backend")
    return await _create_actor(repository, name, char, backend)


def _rules_for_actor(
    rules: tuple[ActorIngressRuleRecord, ...],
    actor_id: str,
) -> tuple[ActorIngressRuleRecord, ...]:
    return tuple(rule for rule in rules if rule.actor_id == actor_id)


def _msg(
    source_id: str,
    source_path: str,
    kind: str = "message",
    text: str = "",
) -> IncomingMessage:
    content = [{"type": "text", "text": text}] if text else []
    return IncomingMessage(
        source=MessageSource(id=source_id, path=source_path),
        message_id="1",
        sender_id="u1",
        kind=kind,
        content=content,
    )
