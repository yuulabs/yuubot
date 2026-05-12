"""Source-aware Gateway route projection and resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

import msgspec

from yuubot.core.messages import IncomingMessage, system_source_id
from yuubot.resources.records import ActorIngressRuleRecord
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import ActorIngressRuleORM, ActorORM


class RouteResolutionError(LookupError):
    """Raised when no actor is bound to a message source."""


@dataclass
class ActorIngressRule:
    """Runtime route rule from a source glob to one actor mailbox."""

    actor_id: str
    source_id_pattern: str
    source_path_pattern: str
    kind_patterns: list[str]

    def matches(self, message: IncomingMessage) -> bool:
        return (
            fnmatchcase(message.source.id, self.source_id_pattern)
            and fnmatchcase(message.source.path, self.source_path_pattern)
            and _matches_any(message.kind, self.kind_patterns)
        )


@dataclass
class RouteBindings:
    """Immutable source glob route snapshot."""

    rules: list[ActorIngressRule]

    def resolve(self, message: IncomingMessage) -> list[str]:
        actor_ids = list(
            dict.fromkeys(
                rule.actor_id for rule in self.rules if rule.matches(message)
            )
        )
        if not actor_ids:
            raise RouteResolutionError(_unrouted_message(message))
        return actor_ids

    def actor_ids(self) -> list[str]:
        return list(dict.fromkeys(rule.actor_id for rule in self.rules))

    def binding_count(self) -> int:
        return len(self.rules)


async def load_route_bindings(repository: ResourceRepository) -> RouteBindings:
    rules = await repository.list(ActorIngressRuleORM)
    actors = await repository.list(ActorORM)
    enabled_actor_ids = {actor.id for actor in actors if actor.enabled}
    return build_route_bindings(
        explicit_rules=(
            rule
            for rule in rules
            if rule.actor_id in enabled_actor_ids
        ),
        enabled_actor_ids=enabled_actor_ids,
    )


def build_route_bindings(
    explicit_rules: Iterable[ActorIngressRuleRecord],
    *,
    enabled_actor_ids: Iterable[str] = (),
) -> RouteBindings:
    explicit_rules = list(explicit_rules)
    explicit_rule_ids = set()
    rules = []
    for rule in explicit_rules:
        explicit_rule_ids.add(rule.actor_id)
        if rule.enabled:
            rules.append(_runtime_rule(rule))
    rules.extend(
        _system_rule(actor_id)
        for actor_id in sorted(enabled_actor_ids)
        if actor_id not in explicit_rule_ids or any(
            rule.enabled and rule.actor_id == actor_id for rule in explicit_rules
        )
    )
    return RouteBindings(rules=rules)


def _runtime_rule(rule: ActorIngressRuleRecord) -> ActorIngressRule:
    return msgspec.convert(
        msgspec.to_builtins(rule),
        type=ActorIngressRule,
        strict=False,
    )


def _system_rule(actor_id: str) -> ActorIngressRule:
    return ActorIngressRule(
        actor_id=actor_id,
        source_id_pattern=system_source_id(actor_id),
        source_path_pattern="**",
        kind_patterns=["*"],
    )


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(value, pattern) for pattern in patterns)


def _unrouted_message(message: IncomingMessage) -> str:
    return (
        "no actor bound to source "
        f"{message.source.id!r}/{message.source.path!r} kind={message.kind!r}"
    )
