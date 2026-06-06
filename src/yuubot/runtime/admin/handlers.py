"""Admin HTTP route handlers extracted from app.py.

Each handler is returned by a factory function that receives its
dependencies explicitly — no closure capture, no hidden state.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Coroutine, cast
from urllib.parse import urlencode

import httpx
import msgspec
import yuullm
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

from yuubot.bootstrap.config import AdminConfig
from yuubot.core.integrations import IntegrationFactoryRegistry
from yuubot.core.secrets import Secret, secret_field_names
from yuubot.resources.records import IntegrationRecord, LLMBackendRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM, LLMBackendORM
from yuubot.runtime.plugin_manager import (
    ExternalPluginError,
    ExternalPluginManager,
)


# -- Request Structs: typed boundary for admin HTTP payloads --


class PluginInstallRequest(msgspec.Struct, forbid_unknown_fields=False):
    """Typed boundary for plugin install requests."""

    source_path: str = ""
    install_environment: bool = True
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True
    integration_id: str = ""


class DaemonResponseData(msgspec.Struct, forbid_unknown_fields=False):
    """Typed extraction from daemon JSON responses."""

    data: object = None
    warnings: list[str] = msgspec.field(default_factory=list)
    detail: str = ""


@dataclass
class DaemonClient:
    base_url: str
    daemon_secret: str = ""


@dataclass
class DaemonResponse:
    status_code: int
    body: bytes
    content_type: str = "application/json"


# -- Callable type aliases for dependency injection --
RequestDaemonFn = Callable[..., Coroutine[Any, Any, "DaemonResponse"]]
CreateProviderModelClientFn = Callable[..., "yuullm.Provider"]


# -- Utility helpers --


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


async def _optional_json_body(request: Request) -> dict[str, object] | JSONResponse:
    body = await request.body()
    if not body:
        return {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _error("validation_error", "invalid JSON body", 400)
    if not isinstance(payload, dict):
        return _error("validation_error", "body must be a JSON object", 400)
    return cast(dict[str, object], payload)


def _string_payload_value(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    return value.strip() if isinstance(value, str) else ""


# -- Provider helpers --


def _create_provider_model_client(
    backend: LLMBackendRecord,
    *,
    api_key: str = "",
    base_url: str = "",
) -> yuullm.Provider:
    provider_key = _provider_key(backend)
    provider_api_key = api_key or backend.provider_options.api_key
    provider_base_url = base_url or backend.provider_options.base_url

    if provider_key == "anthropic":
        return yuullm.providers.AnthropicProvider(
            api_key=provider_api_key or None,
            base_url=provider_base_url or None,
        )
    if provider_key == "openrouter":
        return yuullm.providers.OpenRouterProvider(api_key=provider_api_key)
    return yuullm.providers.OpenAIProvider(
        api_key=provider_api_key or None,
        base_url=provider_base_url or None,
        provider_name=provider_key,
    )


def _provider_key(backend: LLMBackendRecord) -> str:
    provider_name = backend.provider_options.provider_name.strip()
    if provider_name:
        return provider_name
    base_url_key = _provider_key_from_base_url(backend.provider_options.base_url)
    if base_url_key:
        return base_url_key
    return backend.yuuagents_provider


def _provider_key_from_base_url(base_url: str) -> str:
    from urllib.parse import urlparse

    hostname = urlparse(base_url).hostname or ""
    if hostname == "api.deepseek.com":
        return "deepseek"
    if hostname == "api.groq.com":
        return "groq"
    if hostname == "generativelanguage.googleapis.com":
        return "google"
    if hostname == "api.x.ai":
        return "xai"
    return ""


def _provider_model_payload(model: yuullm.ProviderModel) -> dict[str, object]:
    payload: dict[str, object] = {"id": model.id}
    if model.display_name is not None:
        payload["displayName"] = model.display_name
    if model.supports_vision is not None:
        payload["supportsVision"] = model.supports_vision
    return payload


def _provider_capabilities_payload(backend: LLMBackendRecord) -> dict[str, bool]:
    capabilities = backend.model_capabilities
    return {
        "chat": capabilities.chat,
        "vision": capabilities.vision,
        "tool_calling": capabilities.tool_calling,
        "reasoning": capabilities.reasoning,
        "embedding": capabilities.embedding,
        "structured_output": capabilities.structured_output,
    }


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


def _daemon_json_body(response: DaemonResponse) -> dict[str, object]:
    try:
        body = json.loads(response.body.decode(errors="replace"))
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _daemon_error_warning(
    response: DaemonResponse,
    body: dict[str, object],
) -> str:
    detail = body.get("detail")
    if isinstance(detail, str) and detail:
        return f"daemon integration request failed: HTTP {response.status_code}: {detail}"
    return f"daemon integration request failed: HTTP {response.status_code}"


# -- Daemon proxy helpers --


def resource_proxy_path(request: Request) -> str:
    path = "/api/resources/" + request.path_params["resource_type"]
    row_id = request.path_params.get("id")
    action = request.path_params.get("action")
    if row_id is not None:
        path += f"/{row_id}"
    if action is not None:
        path += f"/{action}"
    if request.query_params:
        path += "?" + urlencode(tuple(request.query_params.multi_items()))
    return path


async def _stream_daemon_sse(daemon: DaemonClient, path: str) -> StreamingResponse:
    """Proxy daemon SSE events endpoint with streaming."""
    daemon_url = daemon.base_url.rstrip("/") + "/api/admin/conversations/" + path

    async def event_stream():
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "GET",
                    daemon_url,
                    headers={
                        "X-Daemon-Secret": daemon.daemon_secret,
                        "Accept": "text/event-stream",
                    },
                    timeout=httpx.Timeout(600.0, connect=10.0),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        yield f"event: error\ndata: {body.decode(errors='replace')}\n\n"
                        return
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk.decode(errors="replace")
            except httpx.RemoteProtocolError:
                # daemon closed the connection — stream ended, not an error
                return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _request_daemon(
    daemon: DaemonClient,
    path: str,
    *,
    method: str,
    body: bytes = b"",
    content_type: str = "application/json",
) -> DaemonResponse:
    if not daemon.daemon_secret:
        payload = json.dumps(
            {
                "status": "error",
                "code": "misconfigured",
                "detail": "daemon_secret not set",
            },
            ensure_ascii=True,
        ).encode()
        return DaemonResponse(status_code=500, body=payload)
    return await asyncio.to_thread(
        _send_daemon_request,
        daemon,
        path,
        method=method,
        body=body,
        content_type=content_type,
    )


def _send_daemon_request(
    daemon: DaemonClient,
    path: str,
    *,
    method: str,
    body: bytes,
    content_type: str,
) -> DaemonResponse:
    try:
        response = httpx.Client(timeout=httpx.Timeout(10.0)).request(
            method,
            daemon.base_url.rstrip("/") + path,
            content=body or None,
            headers={
                "Content-Type": content_type,
                "X-Daemon-Secret": daemon.daemon_secret,
            },
        )
        return DaemonResponse(
            status_code=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type", "application/json"),
        )
    except httpx.HTTPStatusError as exc:
        return DaemonResponse(
            status_code=exc.response.status_code,
            body=exc.response.content,
            content_type=exc.response.headers.get("content-type", "application/json"),
        )
    except httpx.RequestError as exc:
        payload = json.dumps(
            {
                "status": "error",
                "code": "daemon_unavailable",
                "detail": str(exc),
            },
            ensure_ascii=True,
        ).encode()
        return DaemonResponse(status_code=502, body=payload)


# --
# Route handler factories
# Each returns an ``async def (request) -> Response`` callable ready
# to be passed to ``starlette.routing.Route``.
# --


def make_admin_health_handler(
    *,
    config: AdminConfig,
    resources: Resources,
    daemon: DaemonClient,
    plugin_manager: ExternalPluginManager,
):
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

    return health


def make_integration_kinds_handler(
    *,
    integration_factories: IntegrationFactoryRegistry,
):
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

    return integration_kinds


def make_reveal_integration_secret_handler(
    *,
    resources: Resources,
    integration_factories: IntegrationFactoryRegistry,
):
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

        config_schema = factory.config_schema
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

    return reveal_integration_secret


def make_provider_models_handler(
    *,
    resources: Resources,
    _create_provider_model_client_fn: CreateProviderModelClientFn | None = None,
):
    _create_client = (
        _create_provider_model_client_fn
        if _create_provider_model_client_fn is not None
        else _create_provider_model_client
    )

    async def provider_models(request: Request) -> JSONResponse:
        backend_id = request.path_params["id"]
        payload = await _optional_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        backend = await resources.repository.get(LLMBackendORM, backend_id)
        if backend is None:
            return _error("not_found", "llm backend not found", 404)

        try:
            client = _create_client(
                backend,
                api_key=_string_payload_value(payload, "api_key"),
                base_url=_string_payload_value(payload, "base_url"),
            )
            models = await client.list_models()
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except (OSError, httpx.HTTPError) as exc:
            return _error("provider_model_fetch_failed", str(exc), 502)

        return JSONResponse(
            {
                "status": "ok",
                "data": [_provider_model_payload(model) for model in models],
            }
        )

    return provider_models


def make_validate_provider_handler(
    *,
    resources: Resources,
    _create_provider_model_client_fn: CreateProviderModelClientFn | None = None,
):
    _create_client = (
        _create_provider_model_client_fn
        if _create_provider_model_client_fn is not None
        else _create_provider_model_client
    )

    async def validate_provider(request: Request) -> JSONResponse:
        backend_id = request.path_params["id"]
        payload = await _optional_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        backend = await resources.repository.get(LLMBackendORM, backend_id)
        if backend is None:
            return _error("not_found", "llm backend not found", 404)

        try:
            client = _create_client(
                backend,
                api_key=_string_payload_value(payload, "api_key"),
                base_url=_string_payload_value(payload, "base_url"),
            )
            models = await client.list_models()
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except (OSError, httpx.HTTPError) as exc:
            return JSONResponse(
                {
                    "status": "ok",
                    "data": {
                        "valid": False,
                        "detail": str(exc),
                        "default_model_valid": False,
                        "models": [],
                        "capabilities": _provider_capabilities_payload(backend),
                    },
                }
            )

        model_ids = [model.id for model in models]
        selected_model = backend.default_model
        if not selected_model and backend.models.names:
            selected_model = backend.models.names[0]
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "valid": True,
                    "detail": "",
                    "default_model_valid": (
                        not selected_model or selected_model in set(model_ids)
                    ),
                    "models": [_provider_model_payload(model) for model in models],
                    "capabilities": _provider_capabilities_payload(backend),
                },
            }
        )

    return validate_provider


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


def make_proxy_daemon_resource_handler(
    *,
    daemon: DaemonClient,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon

    async def proxy_daemon_resource(request: Request) -> Response:
        body = await request.body()
        response = await _req(
            daemon,
            resource_proxy_path(request),
            method=request.method,
            body=body,
            content_type=request.headers.get("content-type", "application/json"),
        )
        return Response(
            response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

    return proxy_daemon_resource


def make_proxy_daemon_conversations_handler(
    *,
    daemon: DaemonClient,
    _request_daemon_fn: RequestDaemonFn | None = None,
):
    _req = _request_daemon_fn if _request_daemon_fn is not None else _request_daemon

    async def proxy_daemon_conversations(request: Request) -> Response:
        # SSE events endpoint must be streamed, not buffered
        if request.method == "GET" and request.path_params.get("path", "").endswith("/events"):
            return await _stream_daemon_sse(daemon, request.path_params["path"])
        body = await request.body()
        daemon_path = "/api/admin/conversations"
        path = request.path_params.get("path")
        if path:
            daemon_path += "/" + path
        if request.query_params:
            daemon_path += "?" + urlencode(tuple(request.query_params.multi_items()))
        response = await _req(
            daemon,
            daemon_path,
            method=request.method,
            body=body,
            content_type=request.headers.get("content-type", "application/json"),
        )
        return Response(
            response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

    return proxy_daemon_conversations


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


def make_serve_spa_handler(
    *,
    index_path: Path,
):
    async def serve_spa(request: Request) -> FileResponse:
        """Serve index.html for client-side routing (SPA fallback).

        Returns index.html for any unmatched GET path. The catch-all route
        is appended last so API routes are checked first.
        """
        return FileResponse(
            str(index_path),
            headers={"Cache-Control": "no-cache"},
        )

    return serve_spa
