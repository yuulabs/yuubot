"""yuullm -- Unified streaming LLM interface.

Public API re-exports for convenient access::

    import yuullm

    client = yuullm.YLLMClient(...)
    history = [
        yuullm.system("You are helpful."),
        yuullm.user("What is 2+2?"),
    ]
    stream, store = await client.stream(history)
"""

from .cache_config import CacheConfig, ConstantRate, TrafficEstimator
from .client import YLLMClient
from .pricing import PriceCalculator
from .pool import ProviderPool
from .provider import Provider
from .session import YuuSession
from .types import (
    AttemptRecovery,
    AudioItem,
    CacheControl,
    Content,
    ContentItem,
    Cost,
    FileItem,
    History,
    ImageItem,
    Message,
    MessageContent,
    MessageItem,
    PartialToolCall,
    PromptItem,
    ProtocolItem,
    ProviderModel,
    ProviderSpec,
    ModelBinding,
    CallRecord,
    RawChunkHook,
    Reasoning,
    RedactedThinkingItem,
    Response,
    StreamItem,
    StreamResult,
    Store,
    StreamCursor,
    TextItem,
    ThinkingBlock,
    ThinkingItem,
    Tick,
    ToolArguments,
    ToolCall,
    ToolCallItem,
    ToolOutput,
    ToolResultContent,
    ToolResultItem,
    ToolSpecs,
    Usage,
    coerce_tool_output,
    coerce_tool_output_item,
    is_audio_item,
    is_file_item,
    is_image_item,
    is_redacted_thinking_item,
    is_text_item,
    is_thinking_item,
    is_tool_call_item,
    is_tool_result_item,
    parse_tool_arguments,
    render_item_text,
    render_message_text,
    split_history,
    # Helper functions for constructing messages
    assistant,
    system,
    tool,
    tool_arguments,
    tool_call_item,
    tool_result,
    tools,
    user,
    # Hook helpers
    on_tool_call_name,
)

# Lazy import to avoid hard dependency on provider SDKs at import time
from . import providers

__all__ = [
    # Client
    "YLLMClient",
    # Provider protocol
    "Provider",
    # Pricing
    "PriceCalculator",
    # Cache config
    "CacheConfig",
    "ConstantRate",
    "TrafficEstimator",
    # Stream items
    "Reasoning",
    "ThinkingBlock",
    "PartialToolCall",
    "ToolCall",
    "Response",
    "Tick",
    "AttemptRecovery",
    "StreamCursor",
    "StreamItem",
    "StreamResult",
    "Store",
    # Content item types (TypedDict)
    "ContentItem",
    "ProtocolItem",
    "MessageItem",
    "PromptItem",
    "Content",
    "MessageContent",
    "ThinkingItem",
    "RedactedThinkingItem",
    "ToolCallItem",
    "ToolArguments",
    "ToolOutput",
    "ToolResultContent",
    "ToolResultItem",
    "ToolSpecs",
    "TextItem",
    "ImageItem",
    "AudioItem",
    "CacheControl",
    "FileItem",
    # Message types & helpers
    "Message",
    "History",
    "ProviderModel",
    "system",
    "user",
    "assistant",
    "tool",
    "tools",
    "tool_result",
    "tool_call_item",
    "coerce_tool_output_item",
    "coerce_tool_output",
    "is_audio_item",
    "is_file_item",
    "is_image_item",
    "is_text_item",
    "is_thinking_item",
    "is_redacted_thinking_item",
    "is_tool_call_item",
    "is_tool_result_item",
    "parse_tool_arguments",
    "tool_arguments",
    "render_item_text",
    "render_message_text",
    "split_history",
    # Usage & Cost
    "Usage",
    "Cost",
    # Hook types & helpers
    "RawChunkHook",
    "on_tool_call_name",
    # Providers sub-package
    "providers",
    # Provider pool & session
    "ProviderSpec",
    "ModelBinding",
    "CallRecord",
    "ProviderPool",
    "YuuSession",
]
