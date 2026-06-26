"""Boundary validation for dict[str, object] config fields.

Storage layer keeps raw dicts; validation happens at consumption boundaries
(assembly, factory.create). Validation is permissive: unknown keys pass through
with a warning, type mismatches raise ConfigurationError.
"""

from __future__ import annotations

import logging

import msgspec

from yuubot.core.secrets import Secret, secret_decode_hook

logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when a config dict fails boundary validation."""


class GenerationParams(msgspec.Struct, forbid_unknown_fields=False):
    """Known generation parameters passed to LLM providers."""

    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None


class LLMProviderOptions(msgspec.Struct, forbid_unknown_fields=False):
    """Known provider-level options for LLM backends."""

    base_url: str = ""
    api_key: Secret = msgspec.field(default_factory=lambda: Secret(""))
    timeout: float = 60.0
    max_retries: int = 2


def validate_generation_params(
    raw: dict[str, object], *, context: str = ""
) -> GenerationParams:
    _validate(raw, GenerationParams, context or "generation_params")
    return msgspec.convert(raw, type=GenerationParams, strict=False)


def validate_provider_options(
    raw: dict[str, object], *, context: str = ""
) -> dict[str, object]:
    return _validate(raw, LLMProviderOptions, context or "provider_options")


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
