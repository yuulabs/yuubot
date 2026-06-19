"""OTEL attribute serialization helpers.

Converts yuutrace struct instances to flat OTEL-compatible attribute dicts
following the key naming conventions defined in ytrace_spec.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry.util.types import AttributeValue

if TYPE_CHECKING:
    from .types import CostDelta, LlmUsageDelta, ToolUsageDelta

# ---------------------------------------------------------------------------
# Attribute key constants (single source of truth)
# ---------------------------------------------------------------------------

# Conversation span attributes
ATTR_CONVERSATION_ID = "yuu.conversation.id"
ATTR_AGENT = "yuu.agent"
ATTR_CONVERSATION_TAGS = "yuu.conversation.tags"
ATTR_CONVERSATION_MODEL = "yuu.conversation.model"

# Event names
EVENT_COST = "yuu.cost"
EVENT_LLM_USAGE = "yuu.llm.usage"
EVENT_TOOL_USAGE = "yuu.tool.usage"

# yuu.cost attributes
ATTR_COST_CATEGORY = "yuu.cost.category"
ATTR_COST_CURRENCY = "yuu.cost.currency"
ATTR_COST_AMOUNT = "yuu.cost.amount"
ATTR_COST_SOURCE = "yuu.cost.source"
ATTR_COST_PRICING_ID = "yuu.cost.pricing_id"

# yuu.llm attributes (shared across cost and usage events)
ATTR_LLM_PROVIDER = "yuu.llm.provider"
ATTR_LLM_MODEL = "yuu.llm.model"
ATTR_LLM_REQUEST_ID = "yuu.llm.request_id"

# yuu.llm.usage attributes
ATTR_LLM_USAGE_INPUT_TOKENS = "yuu.llm.usage.input_tokens"
ATTR_LLM_USAGE_OUTPUT_TOKENS = "yuu.llm.usage.output_tokens"
ATTR_LLM_USAGE_CACHE_READ_TOKENS = "yuu.llm.usage.cache_read_tokens"
ATTR_LLM_USAGE_CACHE_WRITE_TOKENS = "yuu.llm.usage.cache_write_tokens"
ATTR_LLM_USAGE_TOTAL_TOKENS = "yuu.llm.usage.total_tokens"

# yuu.tool attributes (shared across cost and usage events)
ATTR_TOOL_NAME = "yuu.tool.name"
ATTR_TOOL_CALL_ID = "yuu.tool.call_id"
ATTR_TOOL_INPUT = "yuu.tool.input"
ATTR_TOOL_OUTPUT = "yuu.tool.output"
ATTR_TOOL_ERROR = "yuu.tool.error"

# yuu.tool.usage attributes
ATTR_TOOL_USAGE_UNIT = "yuu.tool.usage.unit"
ATTR_TOOL_USAGE_QUANTITY = "yuu.tool.usage.quantity"

# Context span attributes
ATTR_CONTEXT_SYSTEM_TOOLS = "yuu.context.system.tools"

# Entity span attributes
ATTR_ENTITY_ID = "yuu.entity.id"
ATTR_ENTITY_TYPE = "yuu.entity.type"
ATTR_ENTITY_PARENT_ID = "yuu.entity.parent_id"
ATTR_ENTITY_TOOL_CALL_ID = "yuu.entity.tool_call_id"
ATTR_ENTITY_CHUNK_INDEX = "yuu.entity.chunk.index"
ATTR_ENTITY_BLOCKS = "yuu.entity.blocks"
ATTR_ENTITY_STATUS = "yuu.entity.status"

# LLM gen span attributes (legacy, kept for backward-compat reading)
ATTR_LLM_GEN_ITEMS = "yuu.llm_gen.items"

# Turn event
EVENT_TURN = "yuu.turn"
ATTR_TURN_ROLE = "yuu.turn.role"
ATTR_TURN_ITEMS = "yuu.turn.items"
ATTR_TURN_START_TIME = "yuu.turn.start_time"

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

type OtelAttributes = dict[str, AttributeValue]


def _set_optional(attrs: OtelAttributes, key: str, value: AttributeValue | None) -> None:
    """Set *key* in *attrs* only when *value* is not ``None``."""
    if value is not None:
        attrs[key] = value


def cost_delta_to_otel(cost: CostDelta) -> OtelAttributes:
    """Serialize a ``CostDelta`` to a flat OTEL attribute dict."""
    attrs: OtelAttributes = {
        ATTR_COST_CATEGORY: cost.category.value,
        ATTR_COST_CURRENCY: cost.currency.value,
        ATTR_COST_AMOUNT: cost.amount,
    }
    _set_optional(attrs, ATTR_COST_SOURCE, cost.source)
    _set_optional(attrs, ATTR_COST_PRICING_ID, cost.pricing_id)
    _set_optional(attrs, ATTR_LLM_PROVIDER, cost.llm_provider)
    _set_optional(attrs, ATTR_LLM_MODEL, cost.llm_model)
    _set_optional(attrs, ATTR_LLM_REQUEST_ID, cost.llm_request_id)
    _set_optional(attrs, ATTR_TOOL_NAME, cost.tool_name)
    _set_optional(attrs, ATTR_TOOL_CALL_ID, cost.tool_call_id)
    return attrs


def llm_usage_to_otel(usage: LlmUsageDelta) -> OtelAttributes:
    """Serialize a ``LlmUsageDelta`` to a flat OTEL attribute dict."""
    attrs: OtelAttributes = {
        ATTR_LLM_PROVIDER: usage.provider,
        ATTR_LLM_MODEL: usage.model,
        ATTR_LLM_USAGE_INPUT_TOKENS: usage.input_tokens,
        ATTR_LLM_USAGE_OUTPUT_TOKENS: usage.output_tokens,
        ATTR_LLM_USAGE_CACHE_READ_TOKENS: usage.cache_read_tokens,
        ATTR_LLM_USAGE_CACHE_WRITE_TOKENS: usage.cache_write_tokens,
    }
    _set_optional(attrs, ATTR_LLM_REQUEST_ID, usage.request_id)
    _set_optional(attrs, ATTR_LLM_USAGE_TOTAL_TOKENS, usage.total_tokens)
    return attrs


def tool_usage_to_otel(usage: ToolUsageDelta) -> OtelAttributes:
    """Serialize a ``ToolUsageDelta`` to a flat OTEL attribute dict."""
    attrs: OtelAttributes = {
        ATTR_TOOL_NAME: usage.name,
        ATTR_TOOL_USAGE_UNIT: usage.unit,
        ATTR_TOOL_USAGE_QUANTITY: usage.quantity,
    }
    _set_optional(attrs, ATTR_TOOL_CALL_ID, usage.call_id)
    return attrs
