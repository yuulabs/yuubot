"""Durable provider rows."""

import msgspec


class ProviderRecord(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    protocol: str
    config: dict[str, object]
    last_error: str | None = None
