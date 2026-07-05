"""LLM provider core: protocol registry, vendor adapters, and catalog helpers."""

from .catalog import (
    build_actor_provider,
    is_configured,
    merge_catalog,
    model_card_from_input,
    provider_configured,
    refresh_catalog,
)
from .openai import OpenAIProvider, OpenAIProviderConfig, ScriptedProvider, make_openai_provider, scripted_reply
from .protocol import Provider
from .records import ProviderRecord
from .registry import ProviderRegistry, ProviderSpec, default_registry
from .types import (
    AccountSnapshot,
    ModelCardInput,
    ProviderInput,
    ProviderProtocolSpec,
    ProviderSnapshot,
    ValidationResult,
)

__all__ = [
    "AccountSnapshot",
    "ModelCardInput",
    "OpenAIProvider",
    "OpenAIProviderConfig",
    "Provider",
    "ProviderInput",
    "ProviderProtocolSpec",
    "ProviderRecord",
    "ProviderRegistry",
    "ProviderSnapshot",
    "ProviderSpec",
    "ScriptedProvider",
    "ValidationResult",
    "build_actor_provider",
    "default_registry",
    "is_configured",
    "make_openai_provider",
    "merge_catalog",
    "model_card_from_input",
    "provider_configured",
    "refresh_catalog",
    "scripted_reply",
]
