"""Msgspec records persisted by the resource store."""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

import msgspec

from yuubot.core.validation import GenerationParams, LLMProviderOptions


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


class LoopPolicy(msgspec.Struct):
    """Loop convergence policy for a CapabilitySet (§2.7.2)."""

    rollover_enabled: bool = False
    idle_timeout_s: float = 0.0
    summarize_steps_span: int = 20


class ToolSelection(msgspec.Struct):
    """User-configured tool entry on a CapabilitySet (storage view, §3.1)."""

    tool_name: str
    user_fields: dict[str, object] = msgspec.field(default_factory=dict)


class CapabilitySetRecord(msgspec.Struct):
    """Reusable execution and prompt-visible capability bundle (§2.7.1).

    Storage view: only references (``integration_ids`` → IntegrationRecord.id)
    + own configuration. No read-model snapshots. Tools are explicitly listed
    in ``tools`` — there is no implicit injection at assembly time.
    """

    name: str
    description: str = ""
    workspace_path: str = ""
    tools: tuple[ToolSelection, ...] = ()
    integration_ids: tuple[str, ...] = ()   # FK to IntegrationRecord.id
    loop_policy: LoopPolicy = msgspec.field(default_factory=LoopPolicy)
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
