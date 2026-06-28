"""External plugin manifest types, parsing, and validation."""

from __future__ import annotations

import functools
from pathlib import Path

import msgspec
import yaml

from yuubot.core.capabilities import CapabilityEffect

# ── Error ──────────────────────────────────────────────────────────


class ExternalPluginError(ValueError):
    """Raised when an external plugin package or manifest is invalid."""


# ── Capability / Route specs ────────────────────────────────────────


class ExternalPluginResult(msgspec.Struct, forbid_unknown_fields=False):
    value: object = None


class ExternalPluginRoute(msgspec.Struct, forbid_unknown_fields=False):
    path: str
    method: str = "POST"
    description: str = ""


class ExternalPluginIngressSpec(msgspec.Struct, forbid_unknown_fields=False):
    routes: list[ExternalPluginRoute] = msgspec.field(default_factory=list)


class ExternalPluginFunctionSpec(msgspec.Struct, forbid_unknown_fields=False):
    name: str
    description: str = ""
    params: dict[str, dict[str, object]] = msgspec.field(default_factory=dict)
    returns: str = "object"
    effect: CapabilityEffect = "read"


class ExternalPluginFacadeSpec(msgspec.Struct, forbid_unknown_fields=False):
    namespace: str
    functions: list[ExternalPluginFunctionSpec] = msgspec.field(default_factory=list)


class ExternalPluginManifest(msgspec.Struct, forbid_unknown_fields=False, kw_only=True):
    name: str
    entry: str
    version: str = ""
    description: str = ""
    requires_python: str = ""
    ingress: ExternalPluginIngressSpec = msgspec.field(
        default_factory=ExternalPluginIngressSpec,
    )
    facade: ExternalPluginFacadeSpec | None = None
    requires_system: list[str] = msgspec.field(default_factory=list)
    config: dict[str, object] = msgspec.field(default_factory=dict)


# ── Manifest I/O ────────────────────────────────────────────────────


def load_external_plugin_manifest(plugin_dir: Path) -> ExternalPluginManifest:
    """Parse and validate a plugin manifest from *plugin_dir*/manifest.yaml."""
    manifest_path = plugin_dir / "manifest.yaml"
    if not manifest_path.exists():
        raise ExternalPluginError(f"{manifest_path} does not exist")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ExternalPluginError("manifest.yaml root must be an object")
    try:
        manifest = msgspec.convert(raw, type=ExternalPluginManifest, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        raise ExternalPluginError(f"invalid plugin manifest: {exc}") from exc
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: ExternalPluginManifest) -> None:
    if not manifest.name or "/" in manifest.name or "\\" in manifest.name:
        raise ExternalPluginError("manifest.name must be a simple non-empty name")
    if not manifest.entry:
        raise ExternalPluginError("manifest.entry must be set")
    facade = manifest.facade
    if facade is None:
        return
    if not facade.namespace:
        raise ExternalPluginError("manifest.facade.namespace must be set")
    names = [fn.name for fn in facade.functions]
    if len(names) != len(set(names)):
        raise ExternalPluginError("facade function names must be unique")


def _try_load_manifest(plugin_dir: Path) -> ExternalPluginManifest | None:
    try:
        return load_external_plugin_manifest(plugin_dir)
    except ExternalPluginError:
        return None


def _plugin_root_from_archive(staging: Path) -> Path:
    """Find the plugin root inside an extracted zip archive."""
    if (staging / "manifest.yaml").exists():
        return staging
    children = [path for path in staging.iterdir() if path.is_dir()]
    if len(children) == 1 and (children[0] / "manifest.yaml").exists():
        return children[0]
    raise ExternalPluginError(
        "zip must contain manifest.yaml at root or one top-level dir",
    )


# ── Codegen helpers ─────────────────────────────────────────────────


def _capability_id(namespace: str, function_name: str) -> str:
    return f"{namespace}.{function_name}"


def _input_struct(
    plugin_name: str,
    function: ExternalPluginFunctionSpec,
) -> type[msgspec.Struct]:
    param_schema = tuple(
        (name, str(schema.get("type", "object")))
        for name, schema in sorted(function.params.items())
    )
    return _build_input_struct(plugin_name, function.name, param_schema)


@functools.lru_cache(maxsize=128)
def _build_input_struct(
    plugin_name: str,
    function_name: str,
    param_schema: tuple[tuple[str, str], ...],
) -> type[msgspec.Struct]:
    fields = [(name, _schema_type_from_kind(kind)) for name, kind in param_schema]
    type_name = "".join(
        part.capitalize() for part in (plugin_name, function_name, "Input")
    )
    return msgspec.defstruct(
        type_name,
        fields,
        module=__name__,
        forbid_unknown_fields=False,
        kw_only=True,
    )


_SCHEMA_TYPES = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
}


def _schema_type_from_kind(kind: str) -> type:
    return _SCHEMA_TYPES.get(kind, object)
