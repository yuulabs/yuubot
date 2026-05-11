"""Behavior-oriented test helpers."""

from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass

from yuubot.core.integrations.echo import ECHO_CAPABILITY_ID, ECHO_INTEGRATION_PLUGIN_ID
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
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CharacterORM,
    IntegrationORM,
    LLMBackendORM,
)


def build_im_send_argv(
    config_path: str,
    *,
    text: str,
    uid: int | None = None,
    gid: int | None = None,
) -> str:
    del config_path
    message = json.dumps([{"type": "text", "text": text}], ensure_ascii=False)
    parts = ["ybot", "im", "send"]
    if uid is not None:
        parts.extend(["--uid", str(uid)])
    if gid is not None:
        parts.extend(["--gid", str(gid)])
    command = " ".join(parts) + " -- " + shlex.quote(message)
    return json.dumps({"command": command}, ensure_ascii=False)


def sent_texts(sent: list[dict]) -> list[str]:
    """Extract text segments from captured recorder_api send_msg bodies."""
    texts: list[str] = []
    for body in sent:
        for seg in body.get("message", []):
            if seg.get("type") == "text":
                texts.append(seg.get("data", {}).get("text", ""))
    return texts


def llm_system_prompt(calls: list) -> str:
    """Extract concatenated system role text from the first LLM call."""
    if not calls:
        return ""
    for msg in calls[0].get("messages", []):
        if msg.get("role") == "system":
            content = msg.get("content", [])
            return "\n".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            )
    return ""


def llm_user_texts(calls: list) -> list[str]:
    """Extract all user-role text from the first LLM call."""
    if not calls:
        return []
    texts: list[str] = []
    for msg in calls[0].get("messages", []):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            texts.append("\n".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            ))
    return texts


def history_text(history: list) -> str:
    return "\n".join(str(item) for item in history)


async def wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)


@dataclass
class EchoActorResources:
    integration: IntegrationRecord
    character: CharacterRecord
    llm_backend: LLMBackendRecord
    actor: ActorRecord
    ingress_rule: ActorIngressRuleRecord


async def insert_echo_actor_resources(
    repository: ResourceRepository,
    *,
    actor_id: str = "test-actor",
    integration_id: str = "echo-main",
    source_path: str = "channels/test",
    system_prompt: str = "You are a test actor.",
    actor_type: str = "simple_loop",
    max_steps: int = 4,
) -> EchoActorResources:
    """Insert a routable actor wired to an Echo integration."""

    character = await repository.insert(
        CharacterORM,
        make_character_record(actor_id, system_prompt=system_prompt),
    )
    llm_backend = await repository.insert(LLMBackendORM, make_llm_backend_record(actor_id))
    integration = await repository.insert(
        IntegrationORM,
        make_echo_integration_record(integration_id, source_path),
    )
    actor = await repository.insert(
        ActorORM,
        make_actor_record(
            actor_id,
            character=character,
            llm_backend=llm_backend,
            actor_type=actor_type,
            max_steps=max_steps,
        ),
    )
    ingress_rule = await repository.insert(
        ActorIngressRuleORM,
        make_actor_ingress_rule_record(
            integration_id=integration.id,
            source_path=source_path,
            actor_id=actor.id,
        ),
    )
    return EchoActorResources(
        integration=integration,
        character=character,
        llm_backend=llm_backend,
        actor=actor,
        ingress_rule=ingress_rule,
    )


def make_echo_integration_record(
    integration_id: str,
    source_path: str,
) -> IntegrationRecord:
    return IntegrationRecord(
        id=integration_id,
        name=integration_id,
        plugin_id=ECHO_INTEGRATION_PLUGIN_ID,
        config={"source_path": source_path},
    )


def make_actor_ingress_rule_record(
    *,
    integration_id: str,
    source_path: str,
    actor_id: str,
) -> ActorIngressRuleRecord:
    return ActorIngressRuleRecord(
        id=f"{integration_id}:{source_path}:{actor_id}",
        actor_id=actor_id,
        source_id_pattern=integration_id,
        source_path_pattern=source_path,
    )


def make_character_record(
    actor_id: str,
    *,
    system_prompt: str = "You are a test actor.",
) -> CharacterRecord:
    character_id = f"{actor_id}-char"
    return CharacterRecord(
        id=character_id,
        name=character_id,
        description="",
        system_prompt=system_prompt,
        default_prompt_providers=(),
        facade_module="yuubot.core.facade",
        default_hints=CharacterHints(),
    )


def make_llm_backend_record(
    actor_id: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4",
) -> LLMBackendRecord:
    backend_id = f"{actor_id}-backend"
    return LLMBackendRecord(
        id=backend_id,
        name=backend_id,
        yuuagents_provider=provider,
        default_model=model,
        model_capabilities=ModelCapabilities(tool_calling=True),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
    )


def make_actor_record(
    actor_id: str,
    *,
    character: CharacterRecord,
    llm_backend: LLMBackendRecord,
    actor_type: str = "simple_loop",
    max_steps: int = 4,
) -> ActorRecord:
    return ActorRecord(
        id=actor_id,
        name=actor_id,
        type=actor_type,
        character=character,
        llm_backend=llm_backend,
        model="",
        llm_options=YuuAgentLLMOptions(),
        budget=YuuAgentBudget(max_steps=max_steps),
        agent_capabilities=(),
        agent_prompt_providers=(),
        allowed_capability_ids=(ECHO_CAPABILITY_ID,),
        runtime_policy=RuntimePolicy(),
        resource_policy=ResourcePolicy(workspace_access="read_write"),
    )
