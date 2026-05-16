"""Boundary validation for dict[str, object] config fields.

Storage layer keeps raw dicts; validation happens at consumption boundaries
(assembly, factory.create). Validation is permissive: unknown keys pass through
with a warning, type mismatches raise ConfigurationError.
"""

from __future__ import annotations

import logging

import msgspec

from yuubot.core.secrets import secret_decode_hook

logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when a config dict fails boundary validation."""


class StreamOptions(msgspec.Struct, forbid_unknown_fields=False):
    """Known stream/completion options passed to LLM providers."""

    model: str = ""
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None


class LLMProviderOptions(msgspec.Struct, forbid_unknown_fields=False):
    """Known provider-level options for LLM backends."""

    base_url: str = ""
    timeout: float = 60.0
    max_retries: int = 2


class IpykernelCapabilityConfig(msgspec.Struct, forbid_unknown_fields=False):
    """Known keys for the ipykernel capability config."""

    imports: list[object] = msgspec.field(default_factory=list)
    expand_functions: list[str] = msgspec.field(default_factory=list)
    state: dict[str, object] = msgspec.field(default_factory=dict)


class PromptProviderLevelConfig(msgspec.Struct, forbid_unknown_fields=False):
    """Known keys for prompt provider configs."""

    level: str = "summary"


def validate_stream_options(raw: dict[str, object], *, context: str = "") -> dict[str, object]:
    return _validate(raw, StreamOptions, context or "stream_options")


def validate_provider_options(raw: dict[str, object], *, context: str = "") -> dict[str, object]:
    return _validate(raw, LLMProviderOptions, context or "provider_options")


def validate_capability_config(
    provider_key: str,
    raw: dict[str, object],
    *,
    context: str = "",
) -> dict[str, object]:
    schema = _CAPABILITY_SCHEMAS.get(provider_key)
    if schema is None:
        return raw
    return _validate(raw, schema, context or f"capability[{provider_key}]")


def validate_prompt_provider_config(
    provider_key: str,
    raw: dict[str, object],
    *,
    context: str = "",
) -> dict[str, object]:
    schema = _PROMPT_PROVIDER_SCHEMAS.get(provider_key)
    if schema is None:
        return raw
    return _validate(raw, schema, context or f"prompt_provider[{provider_key}]")


def validate_integration_config(
    name: str,
    raw: dict[str, object],
    *,
    schema: type[msgspec.Struct] | None = None,
    context: str = "",
) -> dict[str, object]:
    if schema is None:
        return raw
    return _validate(raw, schema, context or f"integration[{name}]")


def validate_actor_config(
    actor_type: str,
    raw: dict[str, object],
    *,
    schema: type[msgspec.Struct] | None = None,
    context: str = "",
) -> dict[str, object]:
    if schema is None:
        return raw
    return _validate(raw, schema, context or f"actor[{actor_type}]")


_CAPABILITY_SCHEMAS: dict[str, type[msgspec.Struct]] = {
    "ipykernel": IpykernelCapabilityConfig,
}

_PROMPT_PROVIDER_SCHEMAS: dict[str, type[msgspec.Struct]] = {
    "ipykernel": PromptProviderLevelConfig,
}


def _validate(
    raw: dict[str, object],
    schema: type[msgspec.Struct],
    context: str,
) -> dict[str, object]:
    try:
        msgspec.convert(raw, type=schema, strict=False, dec_hook=secret_decode_hook)
    except msgspec.ValidationError as exc:
        raise ConfigurationError(f"{context}: {exc}") from None
    return raw
