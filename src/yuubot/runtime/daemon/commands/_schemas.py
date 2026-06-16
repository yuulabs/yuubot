"""Request schemas — typed boundaries for HTTP payloads.

Each msgspec.Struct validates and deserializes incoming JSON.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Literal, TypeVar

import msgspec

from yuubot.core.validation import LLMProviderOptions, StreamOptions
from yuubot.resources.records import (
    BudgetPolicy,
    CharacterHints,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
    ResourcePolicy,
    RuntimePolicy,
    ToolConfig,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)

in_command_context: ContextVar[bool] = ContextVar("in_command_context", default=False)
StructT = TypeVar("StructT", bound=msgspec.Struct)
ValueT = TypeVar("ValueT")
WorkspaceAccess = Literal["none", "read_only", "read_write"]


# -- Request schemas --


class ActorCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    default_character_id: str
    capability_set_id: str
    default_llm_backend_id: str
    id: str = ""
    type: str = "simple_loop"
    default_model: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    default_llm_options: YuuAgentLLMOptions | msgspec.UnsetType = msgspec.UNSET
    default_budget: YuuAgentBudget | msgspec.UnsetType = msgspec.UNSET
    created_at: datetime | None | msgspec.UnsetType = msgspec.UNSET
    updated_at: datetime | None | msgspec.UnsetType = msgspec.UNSET


class ActorPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    type: str | msgspec.UnsetType = msgspec.UNSET
    default_character_id: str | msgspec.UnsetType = msgspec.UNSET
    capability_set_id: str | msgspec.UnsetType = msgspec.UNSET
    default_llm_backend_id: str | msgspec.UnsetType = msgspec.UNSET
    default_model: str | msgspec.UnsetType = msgspec.UNSET
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    default_llm_options: YuuAgentLLMOptions | msgspec.UnsetType = msgspec.UNSET
    default_budget: YuuAgentBudget | msgspec.UnsetType = msgspec.UNSET


class CapabilitySetPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    integration_capability_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    workspace_path: str | msgspec.UnsetType = msgspec.UNSET
    tool_ids: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    bootstrap_path: str | msgspec.UnsetType = msgspec.UNSET
    enabled_global_skill_refs: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    workspace_skill_root: str | msgspec.UnsetType = msgspec.UNSET
    preexpanded_skill_refs: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    runtime_policy: RuntimePolicy | msgspec.UnsetType = msgspec.UNSET
    prompt_fragments: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET
    permission_limits: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    integration_visible_state: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    agent_tools: tuple[ToolConfig, ...] | msgspec.UnsetType = msgspec.UNSET
    resource_policy: ResourcePolicy | msgspec.UnsetType = msgspec.UNSET


class IntegrationCreateRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str
    id: str = ""
    config: dict[str, object] | msgspec.UnsetType = msgspec.UNSET
    enabled: bool = True
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
    yuuagents_provider: str | msgspec.UnsetType = msgspec.UNSET
    model_capabilities: ModelCapabilities | msgspec.UnsetType = msgspec.UNSET
    models: ModelCatalog | msgspec.UnsetType = msgspec.UNSET
    pricing: PricingTable | msgspec.UnsetType = msgspec.UNSET
    budget: BudgetPolicy | msgspec.UnsetType = msgspec.UNSET
    provider_options: LLMProviderOptions | msgspec.UnsetType = msgspec.UNSET
    default_model: str | msgspec.UnsetType = msgspec.UNSET
    default_stream_options: StreamOptions | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class CharacterPatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    system_prompt: str | msgspec.UnsetType = msgspec.UNSET
    facade_module: str | msgspec.UnsetType = msgspec.UNSET
    default_hints: CharacterHints | msgspec.UnsetType = msgspec.UNSET
    is_builtin: bool | msgspec.UnsetType = msgspec.UNSET
    builtin_version: str | msgspec.UnsetType = msgspec.UNSET
    cloned_from: str | msgspec.UnsetType = msgspec.UNSET
    version: int | msgspec.UnsetType = msgspec.UNSET


class PromptTemplatePatchRequest(msgspec.Struct, forbid_unknown_fields=True):
    id: str | msgspec.UnsetType = msgspec.UNSET
    name: str | msgspec.UnsetType = msgspec.UNSET
    content: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    is_builtin: bool | msgspec.UnsetType = msgspec.UNSET
    builtin_version: str | msgspec.UnsetType = msgspec.UNSET
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
