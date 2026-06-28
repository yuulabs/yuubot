"""Built-in persona prompt templates and CapabilitySet presets."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Protocol, TypeVar

from tortoise import Model

from yuubot.resources.records import (
    CapabilitySetRecord,
    LoopPolicy,
    ToolSelection,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import CapabilitySetORM

ALL_INTEGRATIONS_SENTINEL = "*"
_PROMPT_PACKAGE = "yuubot.resources.preset_prompts"

_OrmT = TypeVar("_OrmT", bound=Model)


class _HasId(Protocol):
    id: str


@dataclass(frozen=True)
class CapabilitySetPreset:
    record: CapabilitySetRecord


@dataclass(frozen=True)
class PresetActorDefinition:
    actor_name: str
    persona_prompt: str
    capability_set_id: str


def _record_id(record: _HasId) -> str:
    return record.id


def _load_prompt(filename: str) -> str:
    return resources.files(_PROMPT_PACKAGE).joinpath(filename).read_text(
        encoding="utf-8"
    ).strip()


GENERAL_PERSONA_PROMPT = _load_prompt("GENERAL.md")
SHIORI_PERSONA_PROMPT = _load_prompt("SHIORI.md")

BUILTIN_PERSONA_PROMPTS: dict[str, str] = {
    "general": GENERAL_PERSONA_PROMPT,
    "shiori": SHIORI_PERSONA_PROMPT,
}


# The standard tool set pre-filled into every builtin CapabilitySet (§3.6.1).
# Tools are explicitly listed — the compiler performs no implicit injection.
STANDARD_TOOLS: tuple[ToolSelection, ...] = (
    ToolSelection("bash"),
    ToolSelection("read"),
    ToolSelection("edit"),
    ToolSelection("write"),
    ToolSelection("execute_python"),
    ToolSelection("restart_kernel"),
)

# Sensible default loop convergence policy for builtin presets.
_BUILTIN_LOOP_POLICY = LoopPolicy(
    rollover_enabled=True,
    idle_timeout_s=1800,
    summarize_steps_span=20,
)

BUILTIN_CAPABILITY_PRESETS: tuple[CapabilitySetPreset, ...] = (
    CapabilitySetPreset(
        CapabilitySetRecord(
            id="builtin-capability-general",
            name="general",
            description="Preset general capability set",
            workspace_path="general",
            tools=STANDARD_TOOLS,
            integration_ids=(ALL_INTEGRATIONS_SENTINEL,),
            loop_policy=_BUILTIN_LOOP_POLICY,
        )
    ),
    CapabilitySetPreset(
        CapabilitySetRecord(
            id="builtin-capability-shiori",
            name="shiori",
            description="Preset Shiori capability set",
            workspace_path="shiori",
            tools=STANDARD_TOOLS,
            integration_ids=(ALL_INTEGRATIONS_SENTINEL,),
            loop_policy=_BUILTIN_LOOP_POLICY,
        )
    ),
)

BUILTIN_PRESET_ACTORS: tuple[PresetActorDefinition, ...] = (
    PresetActorDefinition(
        actor_name="General",
        persona_prompt=GENERAL_PERSONA_PROMPT,
        capability_set_id="builtin-capability-general",
    ),
    PresetActorDefinition(
        actor_name="Shiori",
        persona_prompt=SHIORI_PERSONA_PROMPT,
        capability_set_id="builtin-capability-shiori",
    ),
)


async def seed_builtin_presets(repository: ResourceRepository) -> None:
    """Idempotently seed built-in CapabilitySets.

    Persona prompts are code templates for actor creation, not persisted
    resources.
    """
    for preset in BUILTIN_CAPABILITY_PRESETS:
        await _seed_one(repository, CapabilitySetORM, preset.record)


async def _seed_one(
    repository: ResourceRepository,
    row_type: type[_OrmT],
    record: CapabilitySetRecord,
) -> None:
    existing = await repository.get(row_type, _record_id(record))
    if existing is not None:
        return
    with repository.store.db.activate():
        clash = await row_type.filter(name=record.name).exists()
    if clash:
        return
    await repository.insert(row_type, record)
