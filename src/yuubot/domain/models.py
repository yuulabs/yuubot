"""Durable model selection types."""

from typing import TypeAlias

import msgspec


class AliasModelSelector(
    msgspec.Struct,
    frozen=True,
    tag="alias",
    tag_field="type",
):
    alias: str


class ExactModelSelector(
    msgspec.Struct,
    frozen=True,
    tag="exact",
    tag_field="type",
):
    endpoint_id: str
    model: str


ModelSelector: TypeAlias = AliasModelSelector | ExactModelSelector
