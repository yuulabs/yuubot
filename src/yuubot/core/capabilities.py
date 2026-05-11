"""Agent-visible capability contracts and runtime bindings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast

import msgspec

if TYPE_CHECKING:
    from yuubot.core.integrations.context import InvocationContext

CapabilityEffect = Literal["read", "write", "admin"]
InputT = TypeVar("InputT", bound=msgspec.Struct)
OutputT = TypeVar("OutputT", bound=msgspec.Struct)
_NO_DEFAULT = object()


@dataclass
class CapabilitySpec(Generic[InputT, OutputT]):
    """Agent-visible capability whose contract is a msgspec Struct."""

    id: str
    name: str
    description: str
    input_type: type[InputT]
    output_type: type[OutputT]
    namespace: str = ""
    effect: CapabilityEffect = "read"

    @property
    def input_schema(self) -> dict[str, object]:
        return msgspec.json.schema(self.input_type)

    @property
    def output_schema(self) -> dict[str, object]:
        return msgspec.json.schema(self.output_type)

    def decode_input(self, payload: object) -> InputT:
        return _decode_struct(payload, self.input_type)

    def decode_output(self, payload: object | None) -> OutputT:
        return _decode_struct(payload or {}, self.output_type)


@dataclass
class Capability(Generic[InputT, OutputT]):
    """Executable capability: schema + invoke callback. The runtime unit."""

    id: str
    name: str
    description: str
    input_type: type[InputT]
    output_type: type[OutputT]
    invoke: Callable[[InputT, InvocationContext], Awaitable[OutputT]]
    namespace: str = ""
    effect: CapabilityEffect = "read"

    @property
    def spec(self) -> CapabilitySpec[InputT, OutputT]:
        """Project to schema-only view."""
        return CapabilitySpec(
            id=self.id,
            name=self.name,
            description=self.description,
            input_type=self.input_type,
            output_type=self.output_type,
            namespace=self.namespace,
            effect=self.effect,
        )

    @property
    def input_schema(self) -> dict[str, object]:
        return msgspec.json.schema(self.input_type)

    @property
    def output_schema(self) -> dict[str, object]:
        return msgspec.json.schema(self.output_type)

    def decode_input(self, payload: object) -> InputT:
        return _decode_struct(payload, self.input_type)

    def decode_output(self, payload: object | None) -> OutputT:
        return _decode_struct(payload or {}, self.output_type)


def struct_to_dict(
    value: msgspec.Struct,
    *,
    omit_defaults: bool = False,
) -> dict[str, object]:
    builtins = msgspec.to_builtins(value)
    if not isinstance(builtins, dict):
        raise TypeError(f"{type(value).__name__} did not encode to a dict")
    result = cast(dict[str, object], builtins)
    if omit_defaults:
        return _without_struct_defaults(value, result)
    return result


def _decode_struct(value: object, struct_type: type[InputT]) -> InputT:
    if isinstance(value, struct_type):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"{struct_type.__name__} payload must be a mapping")
    return msgspec.convert(dict(value), type=struct_type, strict=False)


def _without_struct_defaults(
    value: msgspec.Struct,
    data: dict[str, object],
) -> dict[str, object]:
    result = dict(data)
    for field in msgspec.structs.fields(type(value)):
        default = _field_default_builtins(field)
        if default is not _NO_DEFAULT and result.get(field.name) == default:
            result.pop(field.name, None)
    return result


def _field_default_builtins(field: msgspec.structs.FieldInfo) -> object:
    if field.default is not msgspec.NODEFAULT:
        return msgspec.to_builtins(field.default)
    if field.default_factory is not msgspec.NODEFAULT:
        with suppress(Exception):
            return msgspec.to_builtins(field.default_factory())
    return _NO_DEFAULT


AnyCapabilitySpec = CapabilitySpec[Any, Any]
AnyCapability = Capability[Any, Any]
