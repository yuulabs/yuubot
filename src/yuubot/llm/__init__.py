"""LLM provider core: protocol registry, vendor adapters, and catalog helpers."""

from .catalog import (
    build_actor_provider,
    has_pricing_configured,
    is_configured,
    merge_catalog,
    model_card_from_input,
    model_card_wire,
    provider_configured,
    refresh_catalog,
)
from .openai import OpenAIProvider, OpenAIProviderConfig, make_openai_provider
from .scripted import ScriptedProvider, scripted_reply
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
    "has_pricing_configured",
    "is_configured",
    "make_openai_provider",
    "merge_catalog",
    "model_card_from_input",
    "model_card_wire",
    "provider_configured",
    "refresh_catalog",
    "scripted_reply",
]
