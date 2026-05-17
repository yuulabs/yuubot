"""Admin service runtime."""

from __future__ import annotations

import asyncio
import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import msgspec
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from yuubot.bootstrap.config import AdminConfig, BootstrapConfig
from yuubot.bootstrap.layout import DataLayout
from yuubot.core.integrations import (
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.secrets import Secret, secret_field_names
from yuubot.process import ASGIServer, UvicornServer, open_resources
from yuutrace.cli.ui import _build_app as build_trace_app
from yuubot.resources.events import ResourceAction, ResourceChanged
from yuubot.resources.root import Resources
from yuubot.resources.records import IntegrationRecord
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM
from yuubot.runtime.plugin_manager import (
    ExternalPluginError,
    ExternalPluginFactoryLoader,
    ExternalPluginManager,
)


@dataclass
class DaemonClient:
    base_url: str
    daemon_secret: str = ""


@dataclass
class AdminInfrastructure:
    asgi_server: ASGIServer = field(default_factory=UvicornServer)
    integration_factories: IntegrationFactoryRegistry = field(
        default_factory=default_integration_factories
    )


@dataclass
class YuubotAdmin:
    """Running admin service."""

    config: AdminConfig
    resources: Resources
    daemon: DaemonClient
    asgi_server: ASGIServer
    integration_factories: IntegrationFactoryRegistry
    plugin_manager: ExternalPluginManager
    trace_db_path: str = ""

    async def close(self) -> None:
        await self.resources.close()

    def asgi_app(self) -> Starlette:
        return build_admin_asgi_app(
            config=self.config,
            resources=self.resources,
            daemon=self.daemon,
            integration_factories=self.integration_factories,
            plugin_manager=self.plugin_manager,
            trace_db_path=self.trace_db_path,
        )

    async def serve(self) -> None:
        try:
            await self.asgi_server.serve(
                self.asgi_app(),
                host=self.config.host,
                port=self.config.port,
            )
        finally:
            await self.resources.close()


def build_admin_asgi_app(
    *,
    config: AdminConfig,
    resources: Resources,
    daemon: DaemonClient,
    integration_factories: IntegrationFactoryRegistry,
    plugin_manager: ExternalPluginManager | None = None,
    trace_db_path: str = "",
) -> Starlette:
    if plugin_manager is None:
        layout = DataLayout.from_path("~/.yuubot")
        plugin_manager = ExternalPluginManager(
            plugins_dir=layout.plugins_dir,
            data_root=layout.data_dir,
        )

    async def health(_: Request) -> JSONResponse:
        ingress_rules = await resources.repository.list(ActorIngressRuleORM)
        integrations = await resources.repository.list(IntegrationORM)
        return JSONResponse(
            {
                "status": "ok",
                "admin": f"{config.host}:{config.port}",
                "daemon": daemon.base_url,
                "ingress_rules": len(ingress_rules),
                "integrations": len(integrations),
                "plugins": len(plugin_manager.installed_manifests()),
            }
        )

    async def integration_kinds(_: Request) -> JSONResponse:
        kinds = [
            {
                "name": kind.name,
                "description": kind.description,
                "config_schema": kind.config_schema,
                "capabilities": [
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "description": spec.description,
                        "namespace": spec.namespace,
                    }
                    for spec in kind.capabilities
                ],
            }
            for kind in integration_factories.integration_kinds()
        ]
        return JSONResponse({"kinds": kinds})

    async def reveal_integration_secret(request: Request) -> JSONResponse:
        integration_id = request.path_params["id"]
        field = request.path_params["field"]

        record = await resources.repository.get(IntegrationORM, integration_id)
        if record is None:
            return JSONResponse(
                {"status": "error", "code": "not_found", "detail": "integration not found"},
                status_code=404,
            )
        try:
            factory = integration_factories.get(record.name)
        except LookupError as exc:
            return JSONResponse(
                {"status": "error", "code": "validation_error", "detail": str(exc)},
                status_code=400,
            )

        config_schema = getattr(factory, "config_schema", None)
        if field not in secret_field_names(config_schema):
            return JSONResponse(
                {"status": "error", "code": "not_found", "detail": "secret field not found"},
                status_code=404,
            )

        value = record.config.get(field)
        if not isinstance(value, Secret):
            return JSONResponse(
                {"status": "error", "code": "not_found", "detail": "secret not set"},
                status_code=404,
            )
        return JSONResponse({"status": "ok", "data": {"value": value.reveal()}})

    async def list_plugins(_: Request) -> JSONResponse:
        records = {
            record.name: record
            for record in await resources.repository.list(IntegrationORM)
        }
        return JSONResponse(
            {
                "status": "ok",
                "plugins": [
                    {
                        "name": manifest.name,
                        "version": manifest.version,
                        "description": manifest.description,
                        "entry": manifest.entry,
                        "installed": True,
                        "integration_id": (
                            records[manifest.name].id
                            if manifest.name in records
                            else ""
                        ),
                        "enabled": (
                            records[manifest.name].enabled
                            if manifest.name in records
                            else False
                        ),
                    }
                    for manifest in plugin_manager.installed_manifests()
                ],
            }
        )

    async def install_plugin(request: Request) -> JSONResponse:
        payload = await _json_body(request)
        if isinstance(payload, JSONResponse):
            return payload
        source_path = payload.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            return _error("validation_error", "source_path must be set", 400)

        install_environment = payload.get("install_environment", True)
        if not isinstance(install_environment, bool):
            return _error("validation_error", "install_environment must be boolean", 400)
        try:
            manifest = await plugin_manager.install(
                Path(source_path),
                install_environment=install_environment,
            )
            record, action = await _upsert_plugin_integration(
                resources,
                manifest.name,
                payload,
            )
            warnings = await _notify_daemon(
                daemon,
                ResourceChanged(
                    table="integrations",
                    action=cast(ResourceAction, action),
                    row_ids=(record.id,),
                ),
            )
        except ExternalPluginError as exc:
            return _error("validation_error", str(exc), 400)
        except Exception as exc:
            return _error("plugin_install_failed", str(exc), 500)

        return JSONResponse(
            {
                "status": "ok",
                "plugin": {
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                },
                "integration": msgspec.to_builtins(record),
                "warnings": warnings,
            },
            status_code=201,
        )

    async def uninstall_plugin(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        record = await _integration_by_name(resources, name)
        row_id = record.id if record is not None else name
        if record is not None:
            await resources.repository.delete(IntegrationORM, record.id)
            warnings = await _notify_daemon(
                daemon,
                ResourceChanged(
                    table="integrations",
                    action="deleted",
                    row_ids=(record.id,),
                ),
            )
        else:
            warnings = []
        shutil.rmtree(plugin_manager.plugins_dir / name, ignore_errors=True)
        return JSONResponse(
            {
                "status": "ok",
                "integration_id": row_id,
                "warnings": warnings,
            }
        )

    routes: list[Route | Mount] = [
        Route("/healthz", health, methods=("GET",)),
        Route(
            "/api/integration-kinds",
            integration_kinds,
            methods=("GET",),
        ),
        Route(
            "/api/integrations/{id}/secrets/{field}/reveal",
            reveal_integration_secret,
            methods=("GET",),
        ),
        Route("/api/plugins", list_plugins, methods=("GET",)),
        Route("/api/plugins/install", install_plugin, methods=("POST",)),
        Route("/api/plugins/{name}", uninstall_plugin, methods=("DELETE",)),
    ]

    if trace_db_path:
        routes.append(
            Mount("/monitor", app=build_trace_app(db_path=trace_db_path))
        )

    return Starlette(routes=tuple(routes))


async def build_admin(
    config: BootstrapConfig,
    *,
    components: AdminInfrastructure | None = None,
) -> YuubotAdmin:
    config.validate()
    components = components or AdminInfrastructure()
    layout = DataLayout.from_path(config.paths.data_dir)
    layout.ensure()
    resources = await open_resources(config)
    daemon_url = f"http://{config.server.daemon_host}:{config.server.daemon_port}"
    plugin_manager = ExternalPluginManager(
        plugins_dir=layout.plugins_dir,
        data_root=layout.data_dir,
        daemon_host=config.server.daemon_host,
        daemon_port=config.server.daemon_port,
    )
    components.integration_factories.register_loader(
        ExternalPluginFactoryLoader(layout.plugins_dir)
    )

    trace_db_path = str(layout.traces_db_path)

    return YuubotAdmin(
        config=config.admin,
        resources=resources,
        daemon=DaemonClient(
            base_url=daemon_url,
            daemon_secret=config.server.daemon_secret,
        ),
        asgi_server=components.asgi_server,
        integration_factories=components.integration_factories,
        plugin_manager=plugin_manager,
        trace_db_path=trace_db_path,
    )


def _error(code: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "code": code, "detail": detail},
        status_code=status_code,
    )


async def _json_body(request: Request) -> dict[str, object] | JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _error("validation_error", "invalid JSON body", 400)
    if not isinstance(payload, dict):
        return _error("validation_error", "body must be a JSON object", 400)
    return cast(dict[str, object], payload)


async def _upsert_plugin_integration(
    resources: Resources,
    plugin_name: str,
    payload: dict[str, object],
) -> tuple[IntegrationRecord, str]:
    existing = await _integration_by_name(resources, plugin_name)
    config = payload.get("config", {})
    if not isinstance(config, dict):
        raise ExternalPluginError("config must be an object")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ExternalPluginError("enabled must be boolean")
    if existing is not None:
        updated = await resources.repository.update(
            IntegrationORM,
            existing.id,
            config=cast(dict[str, object], config),
            enabled=enabled,
        )
        if updated is None:
            raise ExternalPluginError("integration disappeared during update")
        return updated, "updated"

    integration_id = payload.get("integration_id", plugin_name)
    if not isinstance(integration_id, str) or not integration_id:
        raise ExternalPluginError("integration_id must be a non-empty string")
    inserted = await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id=integration_id,
            name=plugin_name,
            config=cast(dict[str, object], config),
            enabled=enabled,
        ),
    )
    return inserted, "inserted"


async def _integration_by_name(
    resources: Resources,
    name: str,
) -> IntegrationRecord | None:
    for record in await resources.repository.list(IntegrationORM):
        if record.name == name:
            return record
    return None


async def _notify_daemon(
    daemon: DaemonClient,
    event: ResourceChanged,
) -> list[str]:
    if not daemon.daemon_secret:
        return ["daemon_secret not set; daemon refresh was not notified"]
    try:
        await asyncio.to_thread(_post_daemon_refresh, daemon, event)
    except Exception as exc:
        return [f"daemon refresh failed: {exc}"]
    return []


def _post_daemon_refresh(daemon: DaemonClient, event: ResourceChanged) -> None:
    request = urllib.request.Request(
        f"{daemon.base_url}/api/admin/refresh",
        data=json.dumps(event.to_dict(), ensure_ascii=True).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Daemon-Secret": daemon.daemon_secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
