"""Admin wire types and provider validation payloads."""

import msgspec


class ProviderInput(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    protocol: str
    config: dict[str, object]


class ProviderSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    protocol: str
    configured: bool
    last_error: str | None = None
    model_count: int = 0
    configured_model_count: int = 0


class ProviderProtocolSpec(msgspec.Struct, frozen=True, kw_only=True):
    protocol: str
    title: str
    default_endpoint: str
    config_schema: dict[str, object]
    secret_fields: tuple[str, ...]


class ModelCardInput(msgspec.Struct, frozen=True, kw_only=True):
    max_context_tokens: int | None = None
    vision: bool = False
    toolcall: bool = True
    json: bool = True
    input_price_per_million: float = 0
    cached_input_price_per_million: float = 0
    output_price_per_million: float = 0


class ValidationResult(msgspec.Struct, frozen=True, kw_only=True):
    ok: bool
    message: str = ""
    detail: dict[str, object] = msgspec.field(default_factory=dict)


class AccountSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    balance: float | None = None
    currency: str | None = None
    raw: dict[str, object] = msgspec.field(default_factory=dict)
