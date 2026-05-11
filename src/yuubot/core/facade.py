"""Agent-visible integration facade generation and RPC bridge."""

from __future__ import annotations

import asyncio
import json
import keyword
import re
import secrets
import shutil
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import msgspec

from yuubot.core.actors.workspace import safe_actor_path_id
from yuubot.core.capabilities import AnyCapabilitySpec, struct_to_dict
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.core import IntegrationCore

YEXT_PACKAGE = "yext"
YEXT_CONTEXT_MODULE = "yuubot_yext_context"


@dataclass
class FacadeEndpoint:
    host: str
    port: int
    token: str


@dataclass
class ActorFacadeBinding:
    actor_id: str
    agent_id: str
    root: Path
    sys_path: tuple[str, ...]
    startup_code: str
    session_state: dict[str, object]


@dataclass
class FacadeWorkspace:
    """Owns generated yext packages and actor-specific facade bindings."""

    root: Path
    package_name: str = YEXT_PACKAGE

    def generate_catalog(self, capabilities: Iterable[AnyCapabilitySpec]) -> Path:
        catalog_root = self.root / "catalog"
        _replace_dir(catalog_root)
        write_facade_package(
            catalog_root,
            capabilities=capabilities,
            package_name=self.package_name,
        )
        return catalog_root / self.package_name

    def bind_actor(
        self,
        *,
        actor_id: str,
        agent_id: str,
        capabilities: Iterable[AnyCapabilitySpec],
        endpoint: FacadeEndpoint,
    ) -> ActorFacadeBinding:
        path_id = safe_actor_path_id(actor_id)
        actor_root = self.root / "actors" / path_id
        source_root = self.root / "actor-packages" / path_id

        _replace_dir(source_root)
        write_facade_package(
            source_root,
            capabilities=capabilities,
            package_name=self.package_name,
        )
        actor_root.mkdir(parents=True, exist_ok=True)
        _replace_path(actor_root / self.package_name)
        (actor_root / self.package_name).symlink_to(
            source_root / self.package_name,
            target_is_directory=True,
        )
        (actor_root / f"{YEXT_CONTEXT_MODULE}.py").write_text(
            _render_context_module(
                actor_id=actor_id,
                agent_id=agent_id,
                endpoint=endpoint,
            ),
            encoding="utf-8",
        )
        return ActorFacadeBinding(
            actor_id=actor_id,
            agent_id=agent_id,
            root=actor_root,
            sys_path=(str(actor_root),),
            startup_code=(
                f"import {self.package_name}\n"
                f"import {YEXT_CONTEXT_MODULE} as yext_context"
            ),
            session_state={
                "actor_id": actor_id,
                "agent_id": agent_id,
                "facade_package": self.package_name,
            },
        )

    def cleanup_actor(self, actor_id: str) -> None:
        path_id = safe_actor_path_id(actor_id)
        _replace_path(self.root / "actors" / path_id)
        _replace_path(self.root / "actor-packages" / path_id)


@dataclass
class IntegrationInvokeBridge:
    """Local daemon-owned RPC bridge used by generated yext modules."""

    integrations: IntegrationCore
    host: str = "127.0.0.1"
    _token: str = ""
    _server: asyncio.Server | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._token = secrets.token_urlsafe(24)
        self._server = await asyncio.start_server(self._handle_client, self.host, 0)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    @property
    def endpoint(self) -> FacadeEndpoint:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("integration invoke bridge is not started")
        port = cast(int, self._server.sockets[0].getsockname()[1])
        return FacadeEndpoint(host=self.host, port=port, token=self._token)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            response = await self._dispatch(raw)
        except Exception as exc:
            response = _error_response(exc)
        writer.write(json.dumps(response, ensure_ascii=True).encode() + b"\n")
        await writer.drain()
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    async def _dispatch(self, raw: bytes) -> dict[str, object]:
        request = json.loads(raw.decode())
        if not isinstance(request, dict):
            raise TypeError("facade request must be a JSON object")
        if request.get("token") != self._token:
            raise PermissionError("invalid facade bridge token")

        actor_id = _required_str(request, "actor_id")
        capability_id = _required_str(request, "capability_id")
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            raise TypeError("facade payload must be a JSON object")

        output = await self.integrations.invoke(
            actor_id=actor_id,
            capability_id=capability_id,
            payload=dict(payload),
            context=InvocationContext(actor_id=actor_id),
        )
        return {"ok": True, "result": struct_to_dict(output, omit_defaults=True)}


def write_facade_package(
    root: Path,
    *,
    capabilities: Iterable[AnyCapabilitySpec],
    package_name: str = YEXT_PACKAGE,
) -> None:
    """Write a yext package that exposes async capability facade functions."""

    package = root / package_name
    package.mkdir(parents=True, exist_ok=True)

    modules: dict[tuple[str, ...], list[AnyCapabilitySpec]] = {}
    for capability in _unique_capabilities(capabilities):
        modules.setdefault(_module_parts(capability), []).append(capability)

    root_exports = sorted({
        _function_name(capability)
        for capabilities_for_module in modules.values()
        for capability in capabilities_for_module
        if _exports_at_package_root(capability)
    })
    module_exports = sorted({
        _module_parts(capability)[0]
        for capabilities_for_module in modules.values()
        for capability in capabilities_for_module
        if not _exports_at_package_root(capability)
    })
    (package / "__init__.py").write_text(
        _render_package_init(root_exports, module_exports),
        encoding="utf-8",
    )
    (package / "_client.py").write_text(_render_client_module(), encoding="utf-8")
    for parts, module_capabilities in modules.items():
        module_dir = package.joinpath(*parts[:-1])
        module_dir.mkdir(parents=True, exist_ok=True)
        for parent in _parents(package, module_dir):
            init_path = parent / "__init__.py"
            if not init_path.exists():
                init_path.write_text("", encoding="utf-8")
        module_path = module_dir / f"{parts[-1]}.py"
        module_path.write_text(
            _render_module(module_capabilities),
            encoding="utf-8",
        )


def facade_call_path(
    capability: AnyCapabilitySpec,
    *,
    package_name: str = YEXT_PACKAGE,
) -> str:
    function_name = _function_name(capability)
    if _exports_at_package_root(capability):
        return f"{package_name}.{function_name}"
    return f"{package_name}." + ".".join((*_module_parts(capability), function_name))


def _render_context_module(
    *,
    actor_id: str,
    agent_id: str,
    endpoint: FacadeEndpoint,
) -> str:
    return f'''"""Actor-local yext runtime context."""

from __future__ import annotations

HOST = {endpoint.host!r}
PORT = {endpoint.port!r}
TOKEN = {endpoint.token!r}
TIMEOUT_S = 10.0
ACTOR_ID = {actor_id!r}
AGENT_ID = {agent_id!r}
'''


def _render_package_init(root_exports: list[str], module_exports: list[str]) -> str:
    lines = ['"""Generated integration facade package."""', ""]
    for name in root_exports:
        lines.append(f"from .{name} import {name}")
    for name in module_exports:
        lines.append(f"from . import {name}")
    lines.append("")
    lines.append(f"__all__ = {[*root_exports, *module_exports]!r}")
    lines.append("")
    return "\n".join(lines)


def _render_client_module() -> str:
    return f'''"""Generated async RPC client for yext facade functions."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from collections.abc import Mapping
from typing import Any

import {YEXT_CONTEXT_MODULE} as _context


async def invoke(capability_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = {{
        "token": _context.TOKEN,
        "actor_id": _context.ACTOR_ID,
        "capability_id": capability_id,
        "payload": payload,
    }}
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(_context.HOST, _context.PORT),
        timeout=_context.TIMEOUT_S,
    )
    try:
        writer.write(json.dumps(request, ensure_ascii=True).encode() + b"\\n")
        await writer.drain()
        raw_response = await asyncio.wait_for(
            reader.readline(),
            timeout=_context.TIMEOUT_S,
        )
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
    if not raw_response:
        raise RuntimeError("integration facade call returned no response")
    response = json.loads(raw_response.decode())
    if not response.get("ok"):
        error = response.get("error", {{}})
        error_type = error.get("type", "RuntimeError")
        message = error.get("message", "integration facade call failed")
        raise RuntimeError(f"{{error_type}}: {{message}}")
    result = response.get("result", {{}})
    if not isinstance(result, dict):
        raise TypeError("integration facade result must be a JSON object")
    return result


def coerce_payload(value: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return dict(payload)
    if not payload and isinstance(value, Mapping):
        return dict(value)
    return {{"value": value, **payload}}
'''


def _render_module(capabilities: list[AnyCapabilitySpec]) -> str:
    functions = "\n\n".join(_render_function(capability) for capability in capabilities)
    return f'''"""Generated integration capability facade."""

from __future__ import annotations

from typing import Any

from ._client import coerce_payload, invoke

_UNSET = object()

{functions}
'''


def _render_function(capability: AnyCapabilitySpec) -> str:
    function_name = _function_name(capability)
    fields = _struct_fields(capability.input_type)
    doc = _function_doc(capability)
    if not fields:
        return f'''async def {function_name}(value: Any = None, **payload: Any) -> dict[str, Any]:
    """{doc}"""
    return await invoke({capability.id!r}, coerce_payload(value, payload))
'''
    if not _fields_have_valid_parameter_names(fields):
        return f'''async def {function_name}(**payload: Any) -> dict[str, Any]:
    """{doc}"""
    return await invoke({capability.id!r}, dict(payload))
'''
    parameters = _render_parameters(fields)
    assignments = "\n".join(_render_payload_assignment(field) for field in fields)
    return f'''async def {function_name}({parameters}) -> dict[str, Any]:
    """{doc}"""
    data = dict(payload)
{assignments}
    return await invoke({capability.id!r}, data)
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
        if _field_is_required(field):
            required.append(f"{field.name}: Any")
        else:
            optional.append(f"{field.name}: Any = _UNSET")
    return "*, " + ", ".join([*required, *optional, "**payload: Any"])


def _render_payload_assignment(field: msgspec.structs.FieldInfo) -> str:
    if _field_is_required(field):
        return f"    data[{field.name!r}] = {field.name}"
    return (
        f"    if {field.name} is not _UNSET:\n"
        f"        data[{field.name!r}] = {field.name}"
    )


def _field_is_required(field: msgspec.structs.FieldInfo) -> bool:
    return field.default is msgspec.NODEFAULT and field.default_factory is msgspec.NODEFAULT


def _function_doc(capability: AnyCapabilitySpec) -> str:
    lines = [
        capability.description.strip(),
        "",
        "Input schema:",
        _indent(_schema_json(capability.input_schema), "    "),
        "Output schema:",
        _indent(_schema_json(capability.output_schema), "    "),
    ]
    return "\n    ".join(line.replace('"""', r'\"\"\"') for line in lines)


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


def _required_str(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"facade request requires string {key!r}")
    return value


def _error_response(exc: Exception) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }


def _unique_capabilities(
    capabilities: Iterable[AnyCapabilitySpec],
) -> tuple[AnyCapabilitySpec, ...]:
    result: dict[str, AnyCapabilitySpec] = {}
    for capability in capabilities:
        result.setdefault(capability.id, capability)
    return tuple(result.values())


def _replace_dir(path: Path) -> None:
    _replace_path(path)
    path.mkdir(parents=True, exist_ok=True)


def _replace_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
