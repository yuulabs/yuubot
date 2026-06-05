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
from urllib.parse import urlencode

import yuullm
import msgspec
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
import httpx
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from yuubot.bootstrap.config import AdminConfig, BootstrapConfig
from yuubot.bootstrap.layout import DataLayout
from yuubot.core.integrations import (
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.secrets import Secret, secret_field_names
from yuubot.runtime.process import ASGIServer, UvicornServer, open_resources
from yuutrace.cli.ui import _build_app as build_trace_app
from yuubot.resources.records import IntegrationRecord, LLMBackendRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import ActorIngressRuleORM, IntegrationORM, LLMBackendORM
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
class DaemonResponse:
    status_code: int
    body: bytes
    content_type: str = "application/json"


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

    async def provider_models(request: Request) -> JSONResponse:
        backend_id = request.path_params["id"]
        payload = await _optional_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        backend = await resources.repository.get(LLMBackendORM, backend_id)
        if backend is None:
            return _error("not_found", "llm backend not found", 404)

        try:
            client = _create_provider_model_client(
                backend,
                api_key=_string_payload_value(payload, "api_key"),
                base_url=_string_payload_value(payload, "base_url"),
            )
            models = await client.list_models()
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except Exception as exc:
            return _error("provider_model_fetch_failed", str(exc), 502)

        return JSONResponse(
            {
                "status": "ok",
                "data": [_provider_model_payload(model) for model in models],
            }
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
            client = _create_provider_model_client(
                backend,
                api_key=_string_payload_value(payload, "api_key"),
                base_url=_string_payload_value(payload, "base_url"),
            )
            models = await client.list_models()
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except Exception as exc:
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

    async def proxy_daemon_resource(request: Request) -> Response:
        body = await request.body()
        response = await _request_daemon(
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

    async def proxy_daemon_conversations(request: Request) -> Response:
        # SSE events endpoint must be streamed, not buffered
        if request.method == "GET" and request.path_params.get("path", "").endswith("/events"):
            return await _stream_daemon_sse(daemon, request.path_params["path"])
        body = await request.body()
        daemon_path = "/api/conversations"
        path = request.path_params.get("path")
        if path:
            daemon_path += "/" + path
        if request.query_params:
            daemon_path += "?" + urlencode(tuple(request.query_params.multi_items()))
        response = await _request_daemon(
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

    async def proxy_daemon_chat_history(request: Request) -> Response:
        """Proxy GET chat history requests to the daemon.

        Catches legacy read-only /api/chat/* history paths.
        """
        daemon_path = "/api/chat/" + request.path_params["path"]
        if request.query_params:
            daemon_path += "?" + urlencode(tuple(request.query_params.multi_items()))
        response = await _request_daemon(
            daemon,
            daemon_path,
            method="GET",
        )
        return Response(
            response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

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
                "integration": integration,
                "warnings": warnings,
            },
            status_code=201,
        )

    async def uninstall_plugin(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        record = await _integration_by_name(resources, name)
        row_id = record.id if record is not None else name
        if record is not None:
            warnings = await _delete_plugin_integration(daemon, record.id)
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
            "/api/resources/{resource_type}",
            proxy_daemon_resource,
            methods=("GET", "POST"),
        ),
        Route(
            "/api/resources/{resource_type}/{id}",
            proxy_daemon_resource,
            methods=("GET", "PUT", "DELETE"),
        ),
        Route(
            "/api/resources/{resource_type}/{id}/{action}",
            proxy_daemon_resource,
            methods=("POST",),
        ),
        Route(
            "/api/conversations",
            proxy_daemon_conversations,
            methods=("GET", "POST"),
        ),
        Route(
            "/api/conversations/{path:path}",
            proxy_daemon_conversations,
            methods=("GET", "POST"),
        ),
        Route(
            "/api/chat/{path:path}",
            proxy_daemon_chat_history,
            methods=("GET",),
        ),
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
        Route("/api/providers/{id}/models", provider_models, methods=("POST",)),
        Route("/api/providers/{id}/validate", validate_provider, methods=("POST",)),
        Route("/api/plugins", list_plugins, methods=("GET",)),
        Route("/api/plugins/install", install_plugin, methods=("POST",)),
        Route("/api/plugins/{name}", uninstall_plugin, methods=("DELETE",)),
    ]

    if trace_db_path:
        routes.append(
            Mount("/monitor/trace", app=build_trace_app(db_path=trace_db_path))
        )

    # Serve frontend static assets from /assets/
    web_dist = config.web_dist_dir or "web/dist"
    web_path = Path(web_dist).resolve()
    if web_path.is_dir():
        assets_path = web_path / "assets"
        if assets_path.is_dir():
            routes.append(
                Mount(
                    "/assets",
                    app=StaticFiles(directory=str(assets_path)),
                    name="assets",
                )
            )

        # Serve index.html explicitly at the root
        index_path = web_path / "index.html"

        async def serve_spa(request: Request) -> FileResponse:
            """Serve index.html for client-side routing (SPA fallback).

            Returns index.html for any unmatched GET path. The catch-all route
            is appended last so API routes are checked first.
            """
            return FileResponse(
                str(index_path),
                headers={"Cache-Control": "no-cache"},
            )

        routes.append(Route("/{path:path}", serve_spa, methods=("GET",)))
        routes.append(Route("/", serve_spa, methods=("GET",)))

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
    resources = await open_resources(config, migrate=False)
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
    if "api.deepseek.com" in base_url:
        return "deepseek"
    if "api.groq.com" in base_url:
        return "groq"
    if "generativelanguage.googleapis.com" in base_url:
        return "google"
    if "api.x.ai" in base_url:
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


async def _upsert_plugin_integration(
    resources: Resources,
    daemon: DaemonClient,
    plugin_name: str,
    req: PluginInstallRequest,
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
) -> tuple[dict[str, object] | None, list[str]]:
    response = await _request_daemon(
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
) -> list[str]:
    response = await _request_daemon(
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
    daemon_url = daemon.base_url.rstrip("/") + "/api/conversations/" + path

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
    try:
        return await asyncio.to_thread(
            _send_daemon_request,
            daemon,
            path,
            method=method,
            body=body,
            content_type=content_type,
        )
    except Exception as exc:
        payload = json.dumps(
            {
                "status": "error",
                "code": "daemon_unavailable",
                "detail": str(exc),
            },
            ensure_ascii=True,
        ).encode()
        return DaemonResponse(status_code=502, body=payload)


def _send_daemon_request(
    daemon: DaemonClient,
    path: str,
    *,
    method: str,
    body: bytes,
    content_type: str,
) -> DaemonResponse:
    request = urllib.request.Request(
        daemon.base_url.rstrip("/") + path,
        data=body if body else None,
        headers={
            "Content-Type": content_type,
            "X-Daemon-Secret": daemon.daemon_secret,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            return DaemonResponse(
                status_code=response.status,
                body=response.read(),
                content_type=response.headers.get_content_type(),
            )
    except urllib.error.HTTPError as exc:
        return DaemonResponse(
            status_code=exc.code,
            body=exc.read(),
            content_type=exc.headers.get_content_type(),
        )
