"""External integration plugin lifecycle and manifest support."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import msgspec
import yaml

from yuubot.core.capabilities import (
    AnyCapability,
    AnyCapabilitySpec,
    Capability,
    CapabilityEffect,
    CapabilitySpec,
    struct_to_dict,
)
from yuubot.core.gateway import Gateway, IntegrationIngress
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.contracts import IntegrationStorage
from yuubot.core.messages import IncomingMessage, MessageSource
from yuubot.resources.records import IntegrationRecord

logger = logging.getLogger(__name__)

PLUGIN_TOKEN_CONFIG_KEY = "_plugin_token"
_INPUT_TYPES: dict[tuple[str, str, tuple[tuple[str, str], ...]], type[msgspec.Struct]] = {}


class ExternalPluginError(ValueError):
    """Raised when an external plugin package or manifest is invalid."""


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


class ExternalPluginManifest(
    msgspec.Struct,
    forbid_unknown_fields=False,
    kw_only=True,
):
    name: str
    entry: str
    version: str = ""
    description: str = ""
    requires_python: str = ""
    ingress: ExternalPluginIngressSpec = msgspec.field(
        default_factory=ExternalPluginIngressSpec
    )
    facade: ExternalPluginFacadeSpec | None = None
    requires_system: list[str] = msgspec.field(default_factory=list)
    config: dict[str, object] = msgspec.field(default_factory=dict)


class ExternalPluginInboundMessage(msgspec.Struct, forbid_unknown_fields=False):
    integration_id: str
    message_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    kind: str = ""
    text: str = ""
    segments: list[dict[str, object]] = msgspec.field(default_factory=list)
    content: list[dict[str, object]] = msgspec.field(default_factory=list)
    source_path: str = ""
    timestamp: int = 0

    def to_message(self) -> IncomingMessage:
        content = self.content or self.segments or _text_content(self.text)
        fields: dict[str, object] = {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "kind": self.kind,
            "source": MessageSource(path=self.source_path),
            "content": content,
        }
        if self.timestamp:
            fields["timestamp"] = self.timestamp
        return msgspec.convert(fields, type=IncomingMessage, strict=False)


@dataclass
class ExternalPluginStatus:
    name: str
    integration_id: str
    port: int
    healthy: bool = False
    pid: int | None = None


@dataclass
class ExternalPluginProcess:
    integration_id: str
    name: str
    port: int
    process: asyncio.subprocess.Process
    plugin_token: str
    internal_token: str

    def status(self) -> ExternalPluginStatus:
        return ExternalPluginStatus(
            name=self.name,
            integration_id=self.integration_id,
            port=self.port,
            healthy=self.process.returncode is None,
            pid=self.process.pid,
        )


@dataclass
class ExternalPluginManager:
    """Installs and supervises external Python integration plugin processes."""

    plugins_dir: Path
    data_root: Path
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 8780
    name: str = "external-plugins"
    _processes: dict[str, ExternalPluginProcess] = field(
        default_factory=dict,
        init=False,
    )

    async def start(self) -> None:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    async def stop(self) -> None:
        await self.stop_all()

    def loader(self) -> "ExternalPluginFactoryLoader":
        return ExternalPluginFactoryLoader(self.plugins_dir, manager=self)

    def manifest(self, name: str) -> ExternalPluginManifest:
        return load_external_plugin_manifest(self.plugins_dir / name)

    def installed_manifests(self) -> list[ExternalPluginManifest]:
        if not self.plugins_dir.exists():
            return []
        return [
            manifest
            for manifest in (
                _try_load_manifest(path)
                for path in sorted(self.plugins_dir.iterdir())
                if path.is_dir()
            )
            if manifest is not None
        ]

    async def install(
        self,
        source: Path,
        *,
        install_environment: bool = True,
    ) -> ExternalPluginManifest:
        plugin_dir, manifest = await asyncio.to_thread(self._copy_plugin_source, source)
        self._check_system_requirements(manifest)
        if install_environment:
            await self.install_environment(plugin_dir)
        return manifest

    async def install_environment(self, plugin_dir: Path) -> None:
        await _run_process(("uv", "venv", ".venv"), cwd=plugin_dir)
        python = _plugin_python(plugin_dir)
        await _run_process(("uv", "pip", "install", ".", "--python", str(python)), cwd=plugin_dir)

    async def start_plugin(
        self,
        record: IntegrationRecord,
        *,
        storage: IntegrationStorage,
    ) -> ExternalPluginProcess:
        if record.id in self._processes:
            return self._processes[record.id]
        manifest = self.manifest(record.name)
        port = _allocate_port()
        plugin_token = _plugin_token(record)
        internal_token = secrets.token_urlsafe(24)
        plugin_dir = self.plugins_dir / record.name
        storage.data_dir.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            str(_plugin_python(plugin_dir)),
            "-m",
            manifest.entry,
            "--port",
            str(port),
            cwd=plugin_dir,
            env={
                **_process_env(),
                "YUUBOT_DATA_DIR": str(storage.data_dir),
                "YUUBOT_INGEST_URL": (
                    f"http://{self.daemon_host}:{self.daemon_port}/ingest"
                ),
                "YUUBOT_PLUGIN_TOKEN": plugin_token,
                "YUUBOT_INTERNAL_TOKEN": internal_token,
            },
        )
        running = ExternalPluginProcess(
            integration_id=record.id,
            name=record.name,
            port=port,
            process=process,
            plugin_token=plugin_token,
            internal_token=internal_token,
        )
        self._processes[record.id] = running
        await _wait_for_plugin_health(running)
        return running

    async def stop_plugin(self, integration_id: str) -> None:
        running = self._processes.pop(integration_id, None)
        if running is None:
            return
        running.process.terminate()
        try:
            await asyncio.wait_for(running.process.wait(), timeout=5.0)
        except TimeoutError:
            running.process.kill()
            await running.process.wait()

    async def stop_all(self) -> None:
        for integration_id in list(self._processes):
            await self.stop_plugin(integration_id)

    def process_for_integration(self, integration_id: str) -> ExternalPluginProcess:
        try:
            return self._processes[integration_id]
        except KeyError as exc:
            raise LookupError(f"external plugin {integration_id!r} is not running") from exc

    def integration_id_for_token(self, token: str) -> str:
        for running in self._processes.values():
            if running.plugin_token == token:
                return running.integration_id
        raise PermissionError("invalid plugin token")

    def statuses(self) -> list[ExternalPluginStatus]:
        return [running.status() for running in self._processes.values()]

    def _copy_plugin_source(
        self,
        source: Path,
    ) -> tuple[Path, ExternalPluginManifest]:
        source = source.expanduser().resolve()
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "plugin"
            if source.is_dir():
                shutil.copytree(source, staging)
            elif zipfile.is_zipfile(source):
                with zipfile.ZipFile(source) as archive:
                    archive.extractall(staging)
                staging = _plugin_root_from_archive(staging)
            else:
                raise ExternalPluginError("source must be a plugin directory or zip file")

            manifest = load_external_plugin_manifest(staging)
            target = self.plugins_dir / manifest.name
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staging, target)
            return target, manifest

    def _check_system_requirements(self, manifest: ExternalPluginManifest) -> None:
        missing = [name for name in manifest.requires_system if shutil.which(name) is None]
        if missing:
            raise ExternalPluginError(
                "missing required system commands: " + ", ".join(sorted(missing))
            )


@dataclass
class ExternalPluginFactoryLoader:
    plugins_dir: Path
    manager: ExternalPluginManager | None = None

    def load(self, name: str) -> "ExternalPluginFactory | None":
        plugin_dir = self.plugins_dir / name
        if not plugin_dir.exists():
            return None
        return ExternalPluginFactory(
            plugin_dir=plugin_dir,
            manifest=load_external_plugin_manifest(plugin_dir),
            manager=self.manager,
        )

    def all_factories(self) -> Iterable["ExternalPluginFactory"]:
        if not self.plugins_dir.exists():
            return ()
        factories: list[ExternalPluginFactory] = []
        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest = _try_load_manifest(plugin_dir)
            if manifest is None:
                continue
            factories.append(
                ExternalPluginFactory(
                    plugin_dir=plugin_dir,
                    manifest=manifest,
                    manager=self.manager,
                )
            )
        return factories


@dataclass
class ExternalPluginFactory:
    plugin_dir: Path
    manifest: ExternalPluginManifest
    manager: ExternalPluginManager | None = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    @property
    def config_schema(self) -> dict[str, object]:
        return dict(self.manifest.config)

    def capability_specs(self) -> list[AnyCapabilitySpec]:
        facade = self.manifest.facade
        if facade is None:
            return []
        return [
            CapabilitySpec(
                id=_capability_id(facade.namespace, fn.name),
                name=fn.name,
                description=fn.description,
                input_type=_input_struct(self.manifest.name, fn),
                output_type=ExternalPluginResult,
                namespace=facade.namespace,
                effect=fn.effect,
            )
            for fn in facade.functions
        ]

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> "ExternalPluginIntegration":
        if self.manager is None:
            raise RuntimeError("external plugin manager is not configured")
        running = await self.manager.start_plugin(record, storage=storage)
        return ExternalPluginIntegration(
            manifest=self.manifest,
            ingress=gateway.open_integration(record.id),
            process=running,
            manager=self.manager,
        )


@dataclass
class ExternalPluginIntegration:
    manifest: ExternalPluginManifest
    ingress: IntegrationIngress
    process: ExternalPluginProcess
    manager: ExternalPluginManager

    def capabilities(self) -> list[AnyCapability]:
        facade = self.manifest.facade
        if facade is None:
            return []
        return [
            Capability(
                id=_capability_id(facade.namespace, fn.name),
                name=fn.name,
                description=fn.description,
                input_type=_input_struct(self.manifest.name, fn),
                output_type=ExternalPluginResult,
                namespace=facade.namespace,
                effect=fn.effect,
                invoke=self._invoke_function(fn.name),
            )
            for fn in facade.functions
        ]

    async def emit_payload(self, payload: ExternalPluginInboundMessage) -> IncomingMessage:
        message = payload.to_message()
        await self.ingress.emit(message)
        return message

    async def response(
        self,
        target_msg_id: str,
        *,
        msg: str = "",
        poke: str = "",
    ) -> None:
        if not msg and not poke:
            return
        try:
            await _post_json(
                f"http://127.0.0.1:{self.process.port}/__yuubot__/response",
                token=self.process.internal_token,
                payload={
                    "target_msg_id": target_msg_id,
                    "msg": msg,
                    "poke": poke,
                },
            )
        except RuntimeError as exc:
            # Plugins may not implement the response endpoint; skip silently
            # while keeping a single-line warning so debugging is still
            # possible without crashing the actor loop.
            if "HTTP 404" in str(exc):
                return
            logger.warning(
                "external plugin %r response delivery failed: %s",
                self.manifest.name,
                exc,
            )

    async def close(self) -> None:
        await self.manager.stop_plugin(self.process.integration_id)

    def _invoke_function(self, function_name: str):
        async def invoke(
            payload: msgspec.Struct,
            context: InvocationContext,
        ) -> ExternalPluginResult:
            _ = context
            data = struct_to_dict(payload, omit_defaults=True)
            result = await _post_json(
                f"http://127.0.0.1:{self.process.port}/facade/{function_name}",
                token=self.process.internal_token,
                payload=data,
            )
            return ExternalPluginResult(value=result)

        return invoke


def load_external_plugin_manifest(plugin_dir: Path) -> ExternalPluginManifest:
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


async def _post_json(
    url: str,
    *,
    token: str,
    payload: Mapping[str, object],
    timeout_s: float = 10.0,
) -> object:
    return await asyncio.to_thread(_post_json_sync, url, token, payload, timeout_s)


def _post_json_sync(
    url: str,
    token: str,
    payload: Mapping[str, object],
    timeout_s: float,
) -> object:
    data = json.dumps(dict(payload), ensure_ascii=True).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"plugin facade returned HTTP {exc.code}: {detail}") from exc
    if not raw:
        return None
    return json.loads(raw.decode())


async def _wait_for_plugin_health(
    running: ExternalPluginProcess,
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if running.process.returncode is not None:
            raise ExternalPluginError(
                f"plugin {running.name!r} exited with code {running.process.returncode}"
            )
        if await asyncio.to_thread(_health_check_sync, running.port):
            return
        await asyncio.sleep(0.05)
    raise ExternalPluginError(f"plugin {running.name!r} did not become healthy")


def _health_check_sync(port: int) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=0.5) as response:
            response.read()
            return response.status == 200
    except Exception:
        return False


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
    if (staging / "manifest.yaml").exists():
        return staging
    children = [path for path in staging.iterdir() if path.is_dir()]
    if len(children) == 1 and (children[0] / "manifest.yaml").exists():
        return children[0]
    raise ExternalPluginError("zip must contain manifest.yaml at root or one top-level dir")


def _input_struct(
    plugin_name: str,
    function: ExternalPluginFunctionSpec,
) -> type[msgspec.Struct]:
    field_kinds = tuple(
        (name, str(schema.get("type", "object")))
        for name, schema in sorted(function.params.items())
    )
    key = (plugin_name, function.name, field_kinds)
    cached = _INPUT_TYPES.get(key)
    if cached is not None:
        return cached
    fields = [(name, _schema_type(schema)) for name, schema in sorted(function.params.items())]
    type_name = _struct_type_name(plugin_name, function.name)
    struct_type = msgspec.defstruct(
        type_name,
        fields,
        module=__name__,
        forbid_unknown_fields=False,
        kw_only=True,
    )
    _INPUT_TYPES[key] = struct_type
    return struct_type


def _schema_type(schema: Mapping[str, object]) -> type:
    kind = schema.get("type", "object")
    if kind in {"str", "string"}:
        return str
    if kind in {"int", "integer"}:
        return int
    if kind in {"float", "number"}:
        return float
    if kind in {"bool", "boolean"}:
        return bool
    if kind in {"list", "array"}:
        return list
    if kind in {"dict", "object"}:
        return dict
    return object


def _capability_id(namespace: str, function_name: str) -> str:
    return f"{namespace}.{function_name}"


def _struct_type_name(plugin_name: str, function_name: str) -> str:
    return "".join(part.capitalize() for part in (plugin_name, function_name, "Input"))


def _plugin_token(record: IntegrationRecord) -> str:
    value = record.config.get(PLUGIN_TOKEN_CONFIG_KEY)
    if isinstance(value, str) and value:
        return value
    return secrets.token_urlsafe(24)


def _plugin_python(plugin_dir: Path) -> Path:
    python = plugin_dir / ".venv" / "bin" / "python"
    if python.exists():
        return python
    return Path(sys.executable)


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])


async def _run_process(args: tuple[str, ...], *, cwd: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise ExternalPluginError(f"{' '.join(args)} failed: {detail}")


def _text_content(text: str) -> list[dict[str, object]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _process_env() -> dict[str, str]:
    import os

    return dict(os.environ)
