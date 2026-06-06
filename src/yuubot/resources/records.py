"""Msgspec records persisted by the resource store."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar

import msgspec
from yuuagents import ToolSpecConfig

from yuubot.core.validation import LLMProviderOptions, StreamOptions

ConfigT = TypeVar("ConfigT", bound=msgspec.Struct)


class ModelCapabilities(msgspec.Struct):
    chat: bool = True
    vision: bool = False
    tool_calling: bool = False
    reasoning: bool = False
    embedding: bool = False
    structured_output: bool = False


class ModelCatalog(msgspec.Struct):
    names: tuple[str, ...] = ()


class PricingEntry(msgspec.Struct):
    model: str
    input_per_million: float = 0.0
    output_per_million: float = 0.0


class PricingTable(msgspec.Struct):
    entries: tuple[PricingEntry, ...] = ()


class BudgetPolicy(msgspec.Struct):
    daily_usd: float | None = None
    monthly_usd: float | None = None


class LLMBackendRecord(msgspec.Struct):
    """Infra backend config for yuuagents StageConfig.llm."""

    name: str
    yuuagents_provider: str
    model_capabilities: ModelCapabilities
    models: ModelCatalog
    pricing: PricingTable
    budget: BudgetPolicy
    id: str = ""
    provider_options: LLMProviderOptions = msgspec.field(default_factory=LLMProviderOptions)
    default_model: str = ""
    default_stream_options: StreamOptions = msgspec.field(default_factory=StreamOptions)
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


class YuuAgentBudget(msgspec.Struct):
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


class YuuAgentLLMOptions(msgspec.Struct):
    """Actor-level LLM overrides for AgentDefinition.llm/StageConfig.llm."""

    max_tokens: int | None = None
    stream_options: StreamOptions = msgspec.field(default_factory=StreamOptions)


class ToolConfig(msgspec.Struct):
    """One yuuagents AgentDefinition.tools entry."""

    provider_key: str
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


class CharacterHints(msgspec.Struct):
    language: str = "zh-CN"
    tone: str = ""


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


class CharacterRecord(msgspec.Struct):
    name: str
    description: str
    system_prompt: str
    facade_module: str
    default_hints: CharacterHints
    id: str = ""
    is_builtin: bool = False
    builtin_version: str = ""
    cloned_from: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ActorRecord(msgspec.Struct):
    """Gateway-routable agent instance, stored in yuuagents-native shape."""

    name: str
    character: CharacterRecord
    llm_backend: LLMBackendRecord
    model: str
    llm_options: YuuAgentLLMOptions
    budget: YuuAgentBudget
    agent_tools: tuple[ToolConfig, ...]
    allowed_capability_ids: tuple[str, ...]
    runtime_policy: RuntimePolicy
    resource_policy: ResourcePolicy
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
