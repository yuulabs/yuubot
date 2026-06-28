"""Tortoise ORM rows derived from persisted msgspec resource records."""

from __future__ import annotations

from yuubot.resources.records import (
    ActorRecord,
    ActorIngressRuleRecord,
    CapabilitySetRecord,
    ConversationHistoryItemRecord,
    ConversationMessageRecord,
    ConversationRecord,
    IntegrationRecord,
    LLMBackendRecord,
)
from yuubot.resources.store.model_factory import (
    FieldSpec,
    char,
    resource_model,
    text,
)

LLMBackendORM = resource_model(
    "LLMBackendORM",
    LLMBackendRecord,
    table="llm_backends",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(unique=True),
    },
)

IntegrationORM = resource_model(
    "IntegrationORM",
    IntegrationRecord,
    table="integrations",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(),
    },
)

CapabilitySetORM = resource_model(
    "CapabilitySetORM",
    CapabilitySetRecord,
    table="capability_sets",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(unique=True),
        "description": text(),
        "workspace_path": text(),
    },
)

ActorORM = resource_model(
    "ActorORM",
    ActorRecord,
    table="actors",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(unique=True),
        "persona_prompt": text(),
        "capability_set_id": char(),
        "llm_backend_id": char(),
        "skill_scope": char(),
    },
)

ActorIngressRuleORM = resource_model(
    "ActorIngressRuleORM",
    ActorIngressRuleRecord,
    table="actor_ingress_rules",
    module=__name__,
    field_specs={
        "id": char(max_length=512, primary_key=True),
        "source_id_pattern": char(max_length=512),
        "source_path_pattern": char(max_length=1024),
    },
)

ConversationORM = resource_model(
    "ConversationORM",
    ConversationRecord,
    table="conversations",
    module=__name__,
    field_specs={
        "conversation_id": char(max_length=255, primary_key=True),
        "actor_id": char(max_length=255),
        "title": text(),
        "reply_address": text(),
        "created_at": FieldSpec(kind="datetime"),
        "updated_at": FieldSpec(kind="datetime"),
    },
)

ConversationMessageORM = resource_model(
    "ConversationMessageORM",
    ConversationMessageRecord,
    table="conversation_messages",
    module=__name__,
    field_specs={
        "id": FieldSpec(kind="int", primary_key=True),
        "message_id": char(max_length=255),
        "conversation_id": char(max_length=255),
        "role": char(max_length=16),
        "raw_content": text(),
        "timestamp": FieldSpec(kind="int"),
        "created_at": FieldSpec(kind="datetime"),
    },
)

ConversationHistoryItemORM = resource_model(
    "ConversationHistoryItemORM",
    ConversationHistoryItemRecord,
    table="conversation_history_items",
    module=__name__,
    field_specs={
        "id": FieldSpec(kind="int", primary_key=True),
        "conversation_id": char(max_length=255),
        "item_kind": char(max_length=16),
        "item_json": text(),
        "created_at": FieldSpec(kind="datetime"),
    },
)
