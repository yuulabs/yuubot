"""External plugin orchestrator: manager, factory, and integration types."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from yuubot.core.capabilities import (
    AnyCapability,
    AnyCapabilitySpec,
    Capability,
    CapabilitySpec,
    struct_to_dict,
)
from yuubot.core.gateway import Gateway, IntegrationIngress
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.contracts import (
    IntegrationSdkSpec,
    IntegrationStorage,
    ReactionKind,
)
from yuubot.core.messages import IncomingMessage
from yuubot.resources.records import IntegrationRecord

from ._facade import ExternalPluginInboundMessage, post_json
from ._lifecycle import (
    check_system_requirements,
    copy_plugin_source,
    install_plugin_environment,
)
from ._manifest import (
    ExternalPluginManifest,
    ExternalPluginResult,
    _capability_id,
    _input_struct,
    _try_load_manifest,
    load_external_plugin_manifest,
)
from ._process import (
    ExternalPluginProcess,
    ExternalPluginStatus,
    allocate_port,
    plugin_python,
    plugin_token,
    process_env,
    wait_for_plugin_health,
)

logger = logging.getLogger(__name__)


# ── Plugin Manager (orchestrator) ───────────────────────────────────


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
        plugin_dir, manifest = await asyncio.to_thread(
            copy_plugin_source, self.plugins_dir, source,
        )
        check_system_requirements(manifest)
        if install_environment:
            await self.install_environment(plugin_dir)
        return manifest

    async def install_environment(self, plugin_dir: Path) -> None:
        await install_plugin_environment(plugin_dir)

    async def start_plugin(
        self,
        record: IntegrationRecord,
        *,
        storage: IntegrationStorage,
    ) -> ExternalPluginProcess:
        if record.id in self._processes:
            return self._processes[record.id]
        manifest = self.manifest(record.name)
        port = allocate_port()
        token = plugin_token(record)
        internal_token = secrets.token_urlsafe(24)
        plugin_dir = self.plugins_dir / record.name
        storage.data_dir.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            str(plugin_python(plugin_dir)),
            "-m",
            manifest.entry,
            "--port",
            str(port),
            cwd=plugin_dir,
            env={
                **process_env(),
                "YUUBOT_DATA_DIR": str(storage.data_dir),
                "YUUBOT_INGEST_URL": (
                    f"http://{self.daemon_host}:{self.daemon_port}/ingest"
                ),
                "YUUBOT_PLUGIN_TOKEN": token,
                "YUUBOT_INTERNAL_TOKEN": internal_token,
            },
        )
        try:
            running = ExternalPluginProcess(
                integration_id=record.id,
                name=record.name,
                port=port,
                process=process,
                plugin_token=token,
                internal_token=internal_token,
            )
            await wait_for_plugin_health(running)
        except Exception:
            process.kill()
            await process.wait()
            raise
        self._processes[record.id] = running
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

    def process_for_integration(
        self,
        integration_id: str,
    ) -> ExternalPluginProcess:
        try:
            return self._processes[integration_id]
        except KeyError as exc:
            raise LookupError(
                f"external plugin {integration_id!r} is not running",
            ) from exc

    def integration_id_for_token(self, token: str) -> str:
        for running in self._processes.values():
            if running.plugin_token == token:
                return running.integration_id
        raise PermissionError("invalid plugin token")

    def statuses(self) -> list[ExternalPluginStatus]:
        return [running.status() for running in self._processes.values()]


# ── Factory Loader ──────────────────────────────────────────────────


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
                ),
            )
        return factories


# ── Factory ─────────────────────────────────────────────────────────


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

    @property
    def source_path_convention(self) -> str:
        """External plugins set source.path in their inbound HTTP payloads."""
        return (
            "Determined by the plugin itself — path is sent in the `source_path` "
            "field of the inbound message payload. Consult the plugin's own "
            "documentation for the naming scheme it uses."
        )

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

    @property
    def sdk_spec(self) -> IntegrationSdkSpec:
        # External plugins do not ship a yext.* facade module callable from
        # the agent kernel; their capabilities are invoked through the bridge.
        # No SDK surface to expose in the system prompt or kernel imports.
        return IntegrationSdkSpec()

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

    def routes(self, integrations: object) -> list:
        """External plugins have no HTTP routes; they push via /ingest."""
        return []


# ── Integration instance ────────────────────────────────────────────


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
                spec=CapabilitySpec(
                    id=_capability_id(facade.namespace, fn.name),
                    name=fn.name,
                    description=fn.description,
                    input_type=_input_struct(self.manifest.name, fn),
                    output_type=ExternalPluginResult,
                    namespace=facade.namespace,
                    effect=fn.effect,
                ),
                invoke=self._invoke_function(fn.name),
            )
            for fn in facade.functions
        ]

    async def emit_payload(
        self,
        payload: ExternalPluginInboundMessage,
    ) -> IncomingMessage:
        message = payload.to_message()
        await self.ingress.emit(message)
        return message

    async def response(
        self,
        target_msg_id: str,
        *,
        path: str = "",
        msg: str = "",
        react: ReactionKind | None = None,
    ) -> None:
        if not msg and react is None:
            return
        try:
            await post_json(
                f"http://127.0.0.1:{self.process.port}/__yuubot__/response",
                token=self.process.internal_token,
                payload={
                    "target_msg_id": target_msg_id,
                    "path": path,
                    "msg": msg,
                    "react": react or "",
                },
            )
        except RuntimeError as exc:
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
            result = await post_json(
                f"http://127.0.0.1:{self.process.port}/facade/{function_name}",
                token=self.process.internal_token,
                payload=data,
            )
            return ExternalPluginResult(value=result)

        return invoke
