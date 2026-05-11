"""Actor ingress rule CRUD through ResourceRepository."""

from __future__ import annotations

from yuubot.resources.records import ActorIngressRuleRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM


class TestActorIngressRuleLifecycle:
    async def test_insert_rule_creates_new(self, resources: Resources):
        rule = await _insert_rule(
            resources,
            "support-route",
            actor_id="support-agent",
            source_id_pattern="slack-main",
            source_path_pattern="channels/support",
            kind_patterns=("mention", "dm"),
        )

        found = await resources.repository.get(ActorIngressRuleORM, rule.id)

        assert rule.enabled is True
        assert rule.source_id_pattern == "slack-main"
        assert rule.source_path_pattern == "channels/support"
        assert rule.kind_patterns == ("mention", "dm")
        assert found == rule

    async def test_disable_rule(self, resources: Resources):
        rule = await _insert_rule(resources, "support-route")

        disabled = await resources.repository.update(
            ActorIngressRuleORM,
            rule.id,
            enabled=False,
        )

        assert disabled is not None
        assert disabled.enabled is False

    async def test_enable_rule(self, resources: Resources):
        rule = await _insert_rule(resources, "support-route", enabled=False)

        enabled = await resources.repository.update(
            ActorIngressRuleORM,
            rule.id,
            enabled=True,
        )

        assert enabled is not None
        assert enabled.enabled is True

    async def test_delete_rule_removes(self, resources: Resources):
        rule = await _insert_rule(resources, "support-route")

        deleted = await resources.repository.delete(ActorIngressRuleORM, rule.id)
        found = await resources.repository.get(ActorIngressRuleORM, rule.id)

        assert deleted is True
        assert found is None

    async def test_rules_for_actor(self, resources: Resources):
        await _insert_rule(resources, "support-slack", actor_id="support-agent")
        await _insert_rule(resources, "support-github", actor_id="support-agent")
        await _insert_rule(resources, "dev-slack", actor_id="dev-agent")

        result = tuple(
            rule
            for rule in await resources.repository.list(ActorIngressRuleORM)
            if rule.actor_id == "support-agent"
        )

        assert {rule.id for rule in result} == {"support-slack", "support-github"}


async def _insert_rule(
    resources: Resources,
    rule_id: str,
    *,
    actor_id: str = "actor-main",
    source_id_pattern: str = "*",
    source_path_pattern: str = "**",
    kind_patterns: tuple[str, ...] = ("*",),
    enabled: bool = True,
) -> ActorIngressRuleRecord:
    record = ActorIngressRuleRecord(
        id=rule_id,
        actor_id=actor_id,
        source_id_pattern=source_id_pattern,
        source_path_pattern=source_path_pattern,
        kind_patterns=kind_patterns,
        enabled=enabled,
    )
    return await resources.repository.insert(ActorIngressRuleORM, record)
