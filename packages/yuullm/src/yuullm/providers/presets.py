"""Static provider presets used by host applications."""

from __future__ import annotations

import msgspec


class ProviderPreset(msgspec.Struct, frozen=True):
    """Vendor preset declared by yuullm and referenced by host config."""

    identity: str
    api_type: str
    display_name: str
    default_base_url: str


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        identity="openai",
        api_type="openai-compatible",
        display_name="OpenAI",
        default_base_url="https://api.openai.com/v1",
    ),
    "anthropic": ProviderPreset(
        identity="anthropic",
        api_type="anthropic-messages",
        display_name="Anthropic",
        default_base_url="https://api.anthropic.com",
    ),
    "deepseek": ProviderPreset(
        identity="deepseek",
        api_type="openai-chat-completion",
        display_name="DeepSeek",
        default_base_url="https://api.deepseek.com",
    ),
    "openrouter": ProviderPreset(
        identity="openrouter",
        api_type="openai-chat-completion",
        display_name="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
    ),
    "openai-chat-completion": ProviderPreset(
        identity="openai-chat-completion",
        api_type="openai-chat-completion",
        display_name="OpenAI Chat Completions Compatible",
        default_base_url="",
    ),
    "openai-compatible": ProviderPreset(
        identity="openai-compatible",
        api_type="openai-compatible",
        display_name="OpenAI Compatible",
        default_base_url="",
    ),
}


def resolve_provider(identity: str) -> ProviderPreset:
    """Return the static preset for *identity* or raise a config error."""

    try:
        return PROVIDER_PRESETS[identity]
    except KeyError as exc:
        raise ValueError(f"unknown provider_identity {identity!r}") from exc
