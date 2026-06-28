"""Request schemas — typed boundaries for HTTP payloads.

Each msgspec.Struct validates and deserializes incoming JSON.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Literal, TypeVar

import msgspec

from yuubot.core.validation import GenerationParams, LLMProviderOptions
from yuubot.resources.records import (
    BudgetPolicy,
    LoopPolicy,
    ModelConfig,
    RunBudget,
    SkillScope,
    ToolSelection,
)

in_command_context: ContextVar[bool] = ContextVar("in_command_context", default=False)
StructT = TypeVar("StructT", bound=msgspec.Struct)
ValueT = TypeVar("ValueT")
WorkspaceAccess = Literal["none", "read_only", "read_write"]


# -- Request schemas --


class ActorCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    capability_set_id: str
    llm_backend_id: str
    model: str
    id: str = ""
    type: str = "simple_loop"
    persona_prompt: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    skill_scope: SkillScope = "global_and_local"
    generation_override: GenerationParams | msgspec.UnsetType = msgspec.UNSET
    per_run_budget: RunBudget | msgspec.UnsetType = msgspec.UNSET
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET


class ActorPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    type: str | msgspec.UnsetType = msgspec.UNSET
    persona_prompt: str | msgspec.UnsetType = msgspec.UNSET
    capability_set_id: str | msgspec.UnsetType = msgspec.UNSET
    llm_backend_id: str | msgspec.UnsetType = msgspec.UNSET
    model: str | msgspec.UnsetType = msgspec.UNSET
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    skill_scope: SkillScope | msgspec.UnsetType = msgspec.UNSET
    generation_override: GenerationParams | msgspec.UnsetType = msgspec.UNSET
    per_run_budget: RunBudget | msgspec.UnsetType = msgspec.UNSET


class CapabilitySetPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    workspace_path: str | msgspec.UnsetType = msgspec.UNSET
    tools: tuple[ToolSelection, ...] | msgspec.UnsetType = msgspec.UNSET
    integration_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    loop_policy: LoopPolicy | msgspec.UnsetType = msgspec.UNSET


class IntegrationCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    id: str = ""
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool = False
    version: int = 1
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET


class IntegrationPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class LLMBackendPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    provider_identity: str | msgspec.UnsetType = msgspec.UNSET
    model_configs: dict[str, ModelConfig] | msgspec.UnsetType = msgspec.UNSET
    budget: BudgetPolicy | msgspec.UnsetType = msgspec.UNSET
    provider_options: LLMProviderOptions | msgspec.UnsetType = msgspec.UNSET
    default_generation_params: GenerationParams | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class ActorIngressRulePatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    actor_id: str | msgspec.UnsetType = msgspec.UNSET
    source_id_pattern: str | msgspec.UnsetType = msgspec.UNSET
    source_path_pattern: str | msgspec.UnsetType = msgspec.UNSET
    kind_patterns: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class ActorIngressRuleCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    actor_id: str
    id: str = ""
    source_id_pattern: str = "*"
    source_path_pattern: str = "**"
    kind_patterns: tuple[str, ...] = ("*",)
    enabled: bool = True
    version: int = 1
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
