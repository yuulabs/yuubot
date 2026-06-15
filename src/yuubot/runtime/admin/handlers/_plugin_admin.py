"""Plugin install/uninstall admin handlers.

Handler factories for plugin lifecycle (list/install/uninstall)
plus internal helpers for upserting/deleting plugin-backed
integration records in the daemon.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import cast

import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse

from yuubot.resources.records import IntegrationRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import IntegrationORM
from yuubot.runtime.plugin_manager import (
    ExternalPluginError,
    ExternalPluginManager,
)

from ._daemon import _request_daemon
from ._helpers import (
    _daemon_error_warning,
    _daemon_json_body,
    _error,
    _json_body,
)
from ._types import DaemonClient, DaemonResponseData, PluginInstallRequest, RequestDaemonFn


# -- Plugin integration helpers --


async def _upsert_plugin_integration(
    resources: Resources,
    daemon: DaemonClient,
    plugin_name: str,
    req: PluginInstallRequest,
    *,
    _request_daemon_fn: RequestDaemonFn | None = None,
) -> tuple[dict[str, object] | None, list[str]]:
    existing = await _integration_by_name(resources, plugin_name)
    if existing is not None:
        return await _write_plugin_integration(
            daemon,
            path=f"/api/resources/integrations/{existing.id}",
            method="PUT",
            payload={
                "config": req.config,
                "enabled": req.enabled,
            },
            _request_daemon_fn=_request_daemon_fn,
        )

    integration_id = req.integration_id or plugin_name
    if not integration_id:
        raise ExternalPluginError("integration_id must be a non-empty string")
    return await _write_plugin_integration(
        daemon,
        path="/api/resources/integrations",
        method="POST",
        payload={
            "id": integration_id,
            "name": plugin_name,
            "config": req.config,
            "enabled": req.enabled,
        },
        _request_daemon_fn=_request_daemon_fn,
    )


async def _integration_by_name(
    resources: Resources,
    name: str,
) -> IntegrationRecord | None:
    for record in await resources.repository.list(IntegrationORM):
        if record.name == name:
            return record
    return None


async def _write_plugin_integration(
    daemon: DaemonClient,
    *,
    path: str,
    method: str,
    payload: dict[str, object],
    _request_daemon_fn: RequestDaemonFn | None = None,
) -> tuple[dict[str, object] | None, list[str]]:
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon
    response = await _req(
        daemon,
        path,
        method=method,
        body=json.dumps(payload, ensure_ascii=True).encode(),
        content_type="application/json",
    )
    body = _daemon_json_body(response)
    if response.status_code >= 400:
        return None, [_daemon_error_warning(response, body)]
    try:
        resp = msgspec.convert(body, type=DaemonResponseData, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError):
        resp = DaemonResponseData()
    integration = cast(dict[str, object], resp.data) if isinstance(resp.data, dict) else None
    return integration, list(resp.warnings)


async def _delete_plugin_integration(
    daemon: DaemonClient,
    integration_id: str,
    *,
    _request_daemon_fn: RequestDaemonFn | None = None,
) -> list[str]:
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon
    response = await _req(
        daemon,
        f"/api/resources/integrations/{integration_id}",
        method="DELETE",
    )
    body = _daemon_json_body(response)
    if response.status_code >= 400:
        return [_daemon_error_warning(response, body)]
    try:
        resp = msgspec.convert(body, type=DaemonResponseData, strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError):
        resp = DaemonResponseData()
    return list(resp.warnings)


# -- Handler factories --


def make_list_plugins_handler(
    *,
    resources: Resources,
    plugin_manager: ExternalPluginManager,
):
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

    return list_plugins


def make_install_plugin_handler(
    *,
    resources: Resources,
    daemon: DaemonClient,
    plugin_manager: ExternalPluginManager,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    async def install_plugin(request: Request) -> JSONResponse:
        payload = await _json_body(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            req = msgspec.convert(payload, type=PluginInstallRequest, strict=False)
        except (msgspec.ValidationError, msgspec.DecodeError):
            return _error("validation_error", "invalid request body", 400)

        if not req.source_path:
            return _error("validation_error", "source_path must be set", 400)

        try:
            manifest = await plugin_manager.install(
                Path(req.source_path),
                install_environment=req.install_environment,
            )
            integration, warnings = await _upsert_plugin_integration(
                resources,
                daemon,
                manifest.name,
                req,
                _request_daemon_fn=_request_daemon_fn,
            )
        except ExternalPluginError as exc:
            return _error("validation_error", str(exc), 400)
        except (OSError, ValueError) as exc:
            return _error("plugin_install_failed", str(exc), 500)

        return JSONResponse(
            {
                "status": "ok",
                "plugin": {
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                },
                "integration": integration,
                "warnings": warnings,
            },
            status_code=201,
        )

    return install_plugin


def make_uninstall_plugin_handler(
    *,
    resources: Resources,
    daemon: DaemonClient,
    plugin_manager: ExternalPluginManager,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    async def uninstall_plugin(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        record = await _integration_by_name(resources, name)
        row_id = record.id if record is not None else name
        if record is not None:
            warnings = await _delete_plugin_integration(
                daemon, record.id, _request_daemon_fn=_request_daemon_fn
            )
        else:
            warnings = []
        await asyncio.to_thread(shutil.rmtree, plugin_manager.plugins_dir / name, ignore_errors=True)
        return JSONResponse(
            {
                "status": "ok",
                "integration_id": row_id,
                "warnings": warnings,
            }
        )

    return uninstall_plugin
