"""Msgspec records persisted by the resource store."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar

import msgspec

from yuubot.core.validation import GenerationParams, LLMProviderOptions


class ToolSpecConfig(msgspec.Struct):
    level: str = "summary"


ConfigT = TypeVar("ConfigT", bound=msgspec.Struct)


class ModelCapabilities(msgspec.Struct):
    chat: bool = True
    vision: bool = False
    tool_calling: bool = False
    reasoning: bool = False
    embedding: bool = False
    structured_output: bool = False


class Pricing(msgspec.Struct, frozen=True):
    """Pricing for one configured model."""

    input_per_million: float = 0.0
    cached_input_per_million: float = 0.0
    output_per_million: float = 0.0


class ModelConfig(msgspec.Struct, frozen=True):
    """User-maintained configuration for one model name."""

    pricing: Pricing = msgspec.field(default_factory=Pricing)
    capabilities: ModelCapabilities = msgspec.field(default_factory=ModelCapabilities)


class BudgetPolicy(msgspec.Struct):
    daily_usd: float | None = None
    monthly_usd: float | None = None


class LLMBackendRecord(msgspec.Struct):
    """Infra backend config for yuuagents StageConfig.llm."""

    name: str
    provider_identity: str
    model_configs: dict[str, ModelConfig]
    budget: BudgetPolicy
    id: str = ""
    provider_options: LLMProviderOptions = msgspec.field(
        default_factory=LLMProviderOptions
    )
    recommended_model: str = ""
    default_generation_params: GenerationParams = msgspec.field(
        default_factory=GenerationParams
    )
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IntegrationRecord(msgspec.Struct):
    """DB-persisted configuration for an integration.

    ``name`` identifies both the integration kind (e.g. ``"echo"``, ``"qq"``)
    and this record; per-kind aliases belong in ``config``.
    """

    name: str
    config: dict[str, object] = msgspec.field(default_factory=dict)
    id: str = ""
    enabled: bool = True
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def typed_config(self, schema: type[ConfigT]) -> ConfigT:
        """Convert raw config dict to a typed Struct at the consumption boundary."""
        return msgspec.convert(self.config, type=schema, strict=False)


class RunBudget(msgspec.Struct):
    """Direct shape of yuuagents.definition.BudgetConfig."""

    max_steps: int = 0
    max_tokens: int = 0
    max_usd: float = 0.0

    def to_budget_config(self):
        """Convert to yuuagents BudgetConfig.

        Lazily imported to keep the records layer free of yuuagents dependency
        at module load time.
        """
        from yuuagents import BudgetConfig

        return BudgetConfig(
            max_steps=self.max_steps,
            max_tokens=self.max_tokens,
            max_usd=self.max_usd,
        )


class ToolConfig(msgspec.Struct):
    """One yuuagents AgentDefinition.tools entry."""

    tool_name: str
    config: dict[str, object] = msgspec.field(default_factory=dict)
    spec: ToolSpecConfig = msgspec.field(default_factory=ToolSpecConfig)


class PromptTemplateRecord(msgspec.Struct):
    name: str
    content: str = ""
    description: str = ""
    id: str = ""
    is_builtin: bool = False
    builtin_version: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RuntimePolicy(msgspec.Struct):
    """yuubot product policy; execution wiring lives in yuuagents-native fields."""

    memory_enabled: bool = False
    memory_curator_enabled: bool = False
    rollover_enabled: bool = False
    idle_timeout_s: float = 0.0
    summarize_steps_span: int = 20
    strict_usage_sink: bool = False


class ResourcePolicy(msgspec.Struct):
    budget_usd_daily: float | None = None
    concurrency_limit: int = 1
    bridge_nodes: tuple[str, ...] = ()
    workspace_access: Literal["none", "read_only", "read_write"] = "none"


class CapabilitySetRecord(msgspec.Struct):
    """Reusable execution and prompt-visible capability bundle."""

    name: str
    description: str = ""
    integration_capability_ids: tuple[str, ...] = ()
    workspace_path: str = ""
    tool_ids: tuple[str, ...] = ()
    bootstrap_path: str = ""
    enabled_global_skill_refs: tuple[str, ...] = ()
    workspace_skill_root: str = ".agents/skills"
    preexpanded_skill_refs: tuple[str, ...] = ()
    runtime_policy: RuntimePolicy = msgspec.field(default_factory=RuntimePolicy)
    prompt_fragments: tuple[str, ...] = ()
    permission_limits: dict[str, object] = msgspec.field(default_factory=dict)
    integration_visible_state: dict[str, object] = msgspec.field(default_factory=dict)
    agent_tools: tuple[ToolConfig, ...] = ()
    resource_policy: ResourcePolicy = msgspec.field(default_factory=ResourcePolicy)
    id: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ActorRecord(msgspec.Struct):
    """Always-on service identity that routes ingress into conversations."""

    name: str
    persona_prompt: str
    capability_set_id: str
    llm_backend_id: str
    model: str
    generation_override: GenerationParams = msgspec.field(
        default_factory=GenerationParams
    )
    per_run_budget: RunBudget = msgspec.field(default_factory=RunBudget)
    id: str = ""
    type: str = "simple_loop"
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def typed_config(self, schema: type[ConfigT]) -> ConfigT:
        """Convert raw config dict to a typed Struct at the consumption boundary."""
        return msgspec.convert(self.config, type=schema, strict=False)


class ResolvedActor(msgspec.Struct, frozen=True):
    """Turn-time actor read model hydrated from actor-owned ids."""

    actor: ActorRecord
    capability_set: CapabilitySetRecord
    llm_backend: LLMBackendRecord


class ActorIngressRuleRecord(msgspec.Struct):
    id: str
    actor_id: str
    source_id_pattern: str = "*"
    source_path_pattern: str = "**"
    kind_patterns: tuple[str, ...] = ("*",)
    enabled: bool = True
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConversationRecord(msgspec.Struct):
    conversation_id: str
    actor_id: str
    title: str = ""
    reply_address: str = ""
    metadata: dict[str, object] = msgspec.field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConversationMessageRecord(msgspec.Struct):
    message_id: str
    conversation_id: str
    role: str
    raw_content: str
    metadata: dict[str, object]
    timestamp: int
    id: int = 0
    created_at: datetime | None = None


class ConversationHistoryItemRecord(msgspec.Struct):
    """One persisted ``yuullm.PromptItem`` in an ordered conversation history.

    Append-only. ``id`` is the auto-increment integer primary key and acts as
    the canonical sequence number. ``item_kind`` is ``"tools"`` for
    ``yuullm.ToolSpecs`` and ``"message"`` for ``yuullm.Message``.
    ``item_json`` is the stable JSON encoding of the PromptItem.
    """

    id: int = 0
    conversation_id: str = ""
    item_kind: str = ""
    item_json: str = ""
    created_at: datetime | None = None
