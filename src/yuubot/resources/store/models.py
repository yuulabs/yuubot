"""Tortoise ORM rows derived from persisted msgspec resource records."""

from __future__ import annotations

from yuubot.resources.records import (
    ActorRecord,
    ActorIngressRuleRecord,
    CharacterRecord,
    IntegrationRecord,
    LLMBackendRecord,
    PromptTemplateRecord,
    SecretRecord,
)
from yuubot.resources.store.model_factory import char, reference, resource_model, text

SecretORM = resource_model(
    "SecretORM",
    SecretRecord,
    table="secrets",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(unique=True),
        "kind": char(max_length=64),
        "ciphertext": text(),
    },
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
        "name": char(unique=True),
    },
)

PromptTemplateORM = resource_model(
    "PromptTemplateORM",
    PromptTemplateRecord,
    table="prompt_templates",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(unique=True),
        "description": text(),
        "content": text(),
        "builtin_version": char(max_length=64),
    },
)

CharacterORM = resource_model(
    "CharacterORM",
    CharacterRecord,
    table="characters",
    module=__name__,
    field_specs={
        "id": char(primary_key=True),
        "name": char(unique=True),
        "description": text(),
        "system_prompt": text(),
        "builtin_version": char(max_length=64),
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
    },
    # Tortoise adds raw FK columns with an _id suffix:
    # character_id and llm_backend_id.
    references={
        "character": reference(CharacterORM),
        "llm_backend": reference(LLMBackendORM),
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
