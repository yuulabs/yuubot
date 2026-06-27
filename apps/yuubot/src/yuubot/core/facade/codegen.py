"""Facade package code generation for agent-visible integration surfaces."""

from __future__ import annotations

import json
import keyword
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import get_args, get_origin

import msgspec

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.facade.client import render_client_module
from yuubot.core.integrations.contracts import VisibleIntegrationSurface

YEXT_PACKAGE = "yext"


@dataclass(frozen=True)
class _GeneratedCapability:
    integration_id: str
    capability: AnyCapabilitySpec
    function_name: str


def write_facade_package(
    root: Path,
    *,
    surfaces: Iterable[VisibleIntegrationSurface],
    package_name: str = YEXT_PACKAGE,
) -> None:
    """Write a yext package that exposes async capability facade functions.

    Each ``VisibleIntegrationSurface`` contributes its declared capability
    specs; the surface grouping is flattened here so the generated module
    structure (one fn per capability, grouped by namespace) is unchanged.
    """

    package = root / package_name
    package.mkdir(parents=True, exist_ok=True)

    capability_refs: list[tuple[str, AnyCapabilitySpec]] = []
    for surface in surfaces:
        capability_refs.extend(
            (surface.integration_id, capability)
            for capability in surface.capabilities
        )

    modules: dict[tuple[str, ...], list[_GeneratedCapability]] = {}
    for generated in _generated_capability_refs(capability_refs):
        modules.setdefault(_module_parts(generated.capability), []).append(generated)

    root_exports = sorted(
        {
            generated.function_name
            for capabilities_for_module in modules.values()
            for generated in capabilities_for_module
            if _exports_at_package_root(generated.capability)
        }
    )
    module_exports = sorted(
        {
            _module_parts(generated.capability)[0]
            for capabilities_for_module in modules.values()
            for generated in capabilities_for_module
            if not _exports_at_package_root(generated.capability)
        }
    )
    (package / "__init__.py").write_text(
        _render_package_init(root_exports, module_exports),
        encoding="utf-8",
    )
    (package / "_client.py").write_text(render_client_module(), encoding="utf-8")
    for parts, module_capabilities in modules.items():
        module_dir = package.joinpath(*parts[:-1])
        module_dir.mkdir(parents=True, exist_ok=True)
        for parent in _parents(package, module_dir):
            init_path = parent / "__init__.py"
            if not init_path.exists():
                init_path.write_text("", encoding="utf-8")
        module_path = module_dir / f"{parts[-1]}.py"
        module_path.write_text(
            _render_module(module_capabilities, package_name=package_name),
            encoding="utf-8",
        )


def clear_facade_module_cache(package_name: str = YEXT_PACKAGE) -> None:
    """Drop generated facade modules from the host import cache."""
    prefix = f"{package_name}."
    for name in list(sys.modules):
        if name == package_name or name.startswith(prefix):
            sys.modules.pop(name, None)


def facade_call_path(
    capability: AnyCapabilitySpec,
    *,
    package_name: str = YEXT_PACKAGE,
) -> str:
    function_name = _function_name(capability)
    if _exports_at_package_root(capability):
        return f"{package_name}.{function_name}"
    return f"{package_name}." + ".".join((*_module_parts(capability), function_name))


def facade_module_name(
    capability: AnyCapabilitySpec,
    *,
    package_name: str = YEXT_PACKAGE,
) -> str:
    if _exports_at_package_root(capability):
        return package_name
    return f"{package_name}." + ".".join(_module_parts(capability))


def _render_package_init(root_exports: list[str], module_exports: list[str]) -> str:
    lines = ['"""Generated integration facade package."""', ""]
    for name in root_exports:
        lines.append(f"from .{name} import {name}")
    for name in module_exports:
        lines.append(f"from . import {name}")
    lines.append("")
    exports = [*root_exports, *module_exports]
    lines.append(f"__all__ = {exports!r}")
    lines.append("")
    return "\n".join(lines)


def _render_module(
    capabilities: list[_GeneratedCapability],
    *,
    package_name: str,
) -> str:
    functions = "\n\n".join(
        _render_function(generated)
        for generated in capabilities
    )
    exports = [generated.function_name for generated in capabilities]
    return f'''"""Generated integration capability facade."""

from __future__ import annotations

from {package_name}._client import coerce_payload, invoke

__all__ = {exports!r}

_UNSET = object()

{functions}
'''


def _render_function(generated: _GeneratedCapability) -> str:
    capability = generated.capability
    function_name = generated.function_name
    fields = _struct_fields(capability.input_type)
    doc = _function_doc(capability)
    if not fields:
        return f'''async def {function_name}(value: object = None, **payload: object) -> dict[str, object]:
    """{doc}"""
    return await invoke({capability.id!r}, coerce_payload(value, payload), integration_id={generated.integration_id!r})
'''
    if not _fields_have_valid_parameter_names(fields):
        return f'''async def {function_name}(**payload: object) -> dict[str, object]:
    """{doc}"""
    return await invoke({capability.id!r}, dict(payload), integration_id={generated.integration_id!r})
'''
    parameters = _render_parameters(fields)
    assignments = "\n".join(_render_payload_assignment(field) for field in fields)
    return f'''async def {function_name}({parameters}) -> dict[str, object]:
    """{doc}"""
    data = dict(payload)
{assignments}
    return await invoke({capability.id!r}, data, integration_id={generated.integration_id!r})
'''


def _struct_fields(
    struct_type: type[msgspec.Struct],
) -> tuple[msgspec.structs.FieldInfo, ...]:
    return msgspec.structs.fields(struct_type)


def _fields_have_valid_parameter_names(
    fields: tuple[msgspec.structs.FieldInfo, ...],
) -> bool:
    return all(field.name == _identifier(field.name) for field in fields)


def _render_parameters(fields: tuple[msgspec.structs.FieldInfo, ...]) -> str:
    required: list[str] = []
    optional: list[str] = []
    for field in fields:
        type_str = _type_annotation(field.type)
        if _field_is_required(field):
            required.append(f"{field.name}: {type_str}")
        else:
            optional.append(f"{field.name}: {type_str} = _UNSET")
    return "*, " + ", ".join([*required, *optional, "**payload: object"])


def _type_annotation(field_type: object) -> str:
    """Render a Python type object as a string annotation for generated code."""
    if field_type is str:
        return "str"
    if field_type is int:
        return "int"
    if field_type is float:
        return "float"
    if field_type is bool:
        return "bool"
    if field_type is bytes:
        return "bytes"
    if field_type is list:
        return "list[object]"
    if field_type is dict:
        return "dict[str, object]"
    if field_type is object:
        return "object"
    origin = get_origin(field_type)
    if origin is list:
        args = get_args(field_type)
        if args:
            return f"list[{_type_annotation(args[0])}]"
        return "list[object]"
    if origin is dict:
        args = get_args(field_type)
        if len(args) == 2:
            return f"dict[{_type_annotation(args[0])}, {_type_annotation(args[1])}]"
        return "dict[str, object]"
    if origin is set:
        args = get_args(field_type)
        if args:
            return f"set[{_type_annotation(args[0])}]"
        return "set[object]"
    if origin is tuple:
        args = get_args(field_type)
        if args:
            items = ", ".join(_type_annotation(a) for a in args)
            return f"tuple[{items}]"
        return "tuple[object, ...]"
    # For union types (X | Y), check for None
    import types

    if origin is types.UnionType:
        args = get_args(field_type)
        none_args = [a for a in args if a is type(None)]
        other_args = [a for a in args if a is not type(None)]
        if none_args and other_args:
            inner = _type_annotation(other_args[0]) if len(other_args) == 1 else "object"
            return f"{inner} | None"
        return "object"
    # Fallback for msgspec.Struct subclasses and unknown types
    if isinstance(field_type, type) and issubclass(field_type, msgspec.Struct):
        return field_type.__qualname__
    return "object"


def _render_payload_assignment(field: msgspec.structs.FieldInfo) -> str:
    if _field_is_required(field):
        return f"    data[{field.name!r}] = {field.name}"
    return (
        f"    if {field.name} is not _UNSET:\n"
        f"        data[{field.name!r}] = {field.name}"
    )


def _field_is_required(field: msgspec.structs.FieldInfo) -> bool:
    return (
        field.default is msgspec.NODEFAULT
        and field.default_factory is msgspec.NODEFAULT
    )


def _function_doc(capability: AnyCapabilitySpec) -> str:
    lines = [
        capability.description.strip(),
        "",
        "Input schema:",
        _indent(_schema_json(capability.input_schema), "    "),
        "Output schema:",
        _indent(_schema_json(capability.output_schema), "    "),
    ]
    return "\n    ".join(line.replace('"""', r"\"\"\"") for line in lines)


def _schema_json(schema: dict[str, object]) -> str:
    return json.dumps(schema, ensure_ascii=True, sort_keys=True)


def _module_parts(capability: AnyCapabilitySpec) -> tuple[str, ...]:
    if "." in capability.id:
        return tuple(_identifier(part) for part in capability.id.split(".")[:-1])
    if capability.namespace:
        return tuple(_identifier(part) for part in capability.namespace.split("."))
    return (_identifier(capability.id),)


def _function_name(capability: AnyCapabilitySpec) -> str:
    return _identifier(capability.id.split(".")[-1])


def _exports_at_package_root(capability: AnyCapabilitySpec) -> bool:
    return "." not in capability.id and capability.namespace in {"", capability.id}


def _identifier(value: str) -> str:
    result = re.sub(r"\W", "_", value)
    if not result or result[0].isdigit():
        result = f"_{result}"
    if keyword.iskeyword(result):
        return f"{result}_"
    return result


def _parents(root: Path, path: Path) -> Iterable[Path]:
    current = path
    while current != root:
        yield current
        current = current.parent


def _generated_capability_refs(
    capabilities: Iterable[tuple[str, AnyCapabilitySpec]],
) -> list[_GeneratedCapability]:
    refs = list(capabilities)
    counts: dict[str, int] = {}
    for integration_id, capability in refs:
        _ = integration_id
        counts[capability.id] = counts.get(capability.id, 0) + 1
    return [
        _GeneratedCapability(
            integration_id=integration_id,
            capability=capability,
            function_name=_generated_function_name(
                integration_id,
                capability,
                duplicate=counts[capability.id] > 1,
            ),
        )
        for integration_id, capability in refs
    ]


def _generated_function_name(
    integration_id: str,
    capability: AnyCapabilitySpec,
    *,
    duplicate: bool,
) -> str:
    base = _function_name(capability)
    if not duplicate:
        return base
    return f"{base}__{_identifier(integration_id)}"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
