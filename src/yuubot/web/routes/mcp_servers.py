"""MCP server admin and facade routes."""

from __future__ import annotations

from html import escape as html_escape

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from ...app import Yuubot
from ...app.deployment import DeploymentConfig
from ...runtime.mcp import McpServerRecord, normalize_mcp_record
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from .bodies import McpReadResourceBody, McpServerBody


def register_mcp_routes(api: FastAPI, app: Yuubot, deployment: DeploymentConfig) -> None:
    @api.get("/api/mcp-servers")
    async def api_mcp_servers() -> Response:
        return json_response({"items": await app.mcp_server_snapshots()})

    @api.put("/api/mcp-servers/{server_id}")
    async def api_put_mcp_server(server_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, McpServerBody)
            if not body.endpoint_url:
                raise ValueError("endpoint_url is required")
            record = normalize_mcp_record(
                McpServerRecord(
                    id=server_id,
                    name=body.name or server_id,
                    endpoint_url=body.endpoint_url,
                    transport=body.transport,
                    auth_mode=body.auth_mode,
                    oauth_issuer=body.oauth_issuer,
                    oauth_authorization_endpoint=body.oauth_authorization_endpoint,
                    oauth_token_endpoint=body.oauth_token_endpoint,
                    oauth_client_id=body.oauth_client_id,
                    oauth_scope=body.oauth_scope,
                    enabled=body.enabled,
                )
            )
            await app.configure_mcp_server(
                record,
                api_key=body.api_key,
                api_key_header=body.api_key_header,
                api_key_prefix=body.api_key_prefix,
                oauth_client_secret=body.oauth_client_secret,
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(await app.mcp_server_snapshots())

    @api.post("/api/mcp-servers/{server_id}/enable")
    async def api_enable_mcp_server(server_id: str) -> Response:
        if server_id not in app.runtime.mcps.records:
            return error_response(404, "not_found", "MCP server not found")
        state = await app.enable_mcp_server(server_id)
        return json_response({"id": server_id, "state": state})

    @api.post("/api/mcp-servers/{server_id}/disable")
    async def api_disable_mcp_server(server_id: str) -> Response:
        if not await app.disable_mcp_server(server_id):
            return error_response(404, "not_found", "MCP server not found")
        return json_response({"id": server_id, "disabled": True})

    @api.post("/api/mcp-servers/{server_id}/refresh")
    async def api_refresh_mcp_server(server_id: str) -> Response:
        if server_id not in app.runtime.mcps.records:
            return error_response(404, "not_found", "MCP server not found")
        state = await app.refresh_mcp_server(server_id)
        status = 200 if state.status == "ready" else 503
        return json_response({"id": server_id, "state": state}, status=status)

    @api.post("/api/mcp-servers/{server_id}/auth/start")
    async def api_start_mcp_oauth(server_id: str) -> Response:
        if server_id not in app.runtime.mcps.records:
            return error_response(404, "not_found", "MCP server not found")
        try:
            attempt = await app.start_mcp_oauth(server_id, public_url_base=deployment.public_url_base)
        except ValueError as exc:
            return bad_request(exc)
        current = await app.wait_auth_attempt(
            attempt.id,
            predicate=lambda item: isinstance(item.action.get("url"), str) or item.status == "failed",
            timeout=5.0,
        )
        if current is not None:
            attempt = current
        return json_response(attempt, status=202)

    @api.get("/api/mcp-oauth/{attempt_id}/callback", response_class=HTMLResponse)
    async def api_mcp_oauth_callback(attempt_id: str, code: str = "", state: str | None = None, error: str = "") -> Response:
        if error:
            if attempt_id in app.runtime.auth_attempts:
                await app.update_auth_attempt(attempt_id, status="failed", error=error)
            return HTMLResponse("<html><body><h1>Authorization failed</h1><p>You can close this tab.</p></body></html>", status_code=400)
        try:
            await app.complete_mcp_oauth_callback(attempt_id, code=code, state=state)
        except KeyError:
            return HTMLResponse("<html><body><h1>Authorization attempt not found</h1><p>You can close this tab.</p></body></html>", status_code=404)
        except ValueError as exc:
            return HTMLResponse(f"<html><body><h1>Authorization failed</h1><p>{html_escape(str(exc))}</p></body></html>", status_code=400)
        return HTMLResponse("<html><body><h1>Authorization received</h1><p>You can close this tab and return to yuubot.</p></body></html>")

    @api.delete("/api/mcp-servers/{server_id}")
    async def api_delete_mcp_server(server_id: str) -> Response:
        if not await app.delete_mcp_server(server_id):
            return error_response(404, "not_found", "MCP server not found")
        return json_response({"id": server_id, "deleted": True})

    @api.get("/api/mcps/search")
    async def api_mcp_search(query: str = "", kind: str = "", server: str = "") -> Response:
        return json_response({"items": app.runtime.mcps.search(query, kind=kind, server=server)})

    @api.get("/api/mcps/{server_id}/spec/{name}")
    async def api_mcp_spec(server_id: str, name: str) -> Response:
        try:
            return json_response({"server_id": server_id, "name": name, "spec": app.runtime.mcps.get_spec(server_id, name)})
        except KeyError:
            return error_response(404, "not_found", "MCP tool not found")

    @api.post("/api/mcps/{server_id}/invoke/{name}")
    async def api_mcp_invoke(server_id: str, name: str, request: Request) -> Response:
        try:
            arguments = await read_json(request, dict[str, object])
            result = await app.runtime.mcps.invoke(server_id, name, arguments)
        except KeyError:
            return error_response(404, "not_found", "MCP server not found")
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except Exception as exc:
            return error_response(502, "mcp_call_failed", str(exc))
        return json_response(result)

    @api.post("/api/mcps/{server_id}/resources/read")
    async def api_mcp_read_resource(server_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, McpReadResourceBody)
            result = await app.runtime.mcps.read_resource(server_id, body.uri)
        except KeyError:
            return error_response(404, "not_found", "MCP server not found")
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except Exception as exc:
            return error_response(502, "mcp_read_failed", str(exc))
        return json_response(result)
