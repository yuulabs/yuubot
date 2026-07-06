"""Admin HTTP + WebSocket route registration."""

import asyncio
import mimetypes
from collections.abc import Callable

import msgspec
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from ...app import Yuubot
from ...app.cron import (
    create_cron_job,
    delete_cron_job,
    get_cron_job,
    list_cron_jobs,
    pause_cron_job,
    resume_cron_job,
    save_push_subscription,
    vapid_public_key_for,
)
from ...app.deployment import DeploymentConfig
from ...chat.listener import WsListener
from ...domain.messages import ModelCard
from ...domain.records import ActorRecord, RouteBody, RouteRecord
from ...integrations import IntegrationRecord
from ...llm import ProviderInput, has_pricing_configured, model_card_wire
from ...llm.types import ModelCardInput, ProviderSnapshot
from ...domain.messages import ActorMessage
from ...runtime.inbound import MailboxUnavailableError
from ...runtime.kv import (
    KvBadRequestError,
    KvConflictError,
    KvPutBody,
    document_snapshot,
    normalize_key,
    parse_if_match,
)
from ...runtime.shares import (
    ShareBadRequestError,
    ShareNotFoundError,
    SharePublishError,
    share_grant_snapshot,
)
from ...runtime.tasks import task_record_snapshot
from ...util.secrets import merge_redacted_config
from ..auth import LoginBody, SessionStore
from ..client_ip import client_ip_from_scope, is_loopback
from ..files import actor_workspace, delete_entries, directory_snapshot, make_directory, move_entries, rename_entry, save_uploads, workspace_path
from ..html import html_page
from ..request import bad_request, read_json
from ..responses import error_response, json_response
from ..errors import internal_error_detail, internal_error_message
from ..ws import handle_ws_command
from .bodies import CreateCronJobBody, PublishShareBody, PushSubscriptionBody, SubmitTaskBody, WorkspaceDeleteBody, WorkspaceMkdirBody, WorkspaceMoveBody, WorkspaceRenameBody
from ._helpers import react_dist_dir, route_exists


def create_admin_app(
    app: Yuubot,
    deployment: DeploymentConfig,
    sessions: SessionStore,
    *,
    on_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    api = FastAPI()
    api.state.deployment = deployment
    api.state.sessions = sessions
    trusted = frozenset(deployment.trusted_proxies)

    def client_is_loopback(request: Request) -> bool:
        return is_loopback(client_ip_from_scope(request.scope, trusted))

    react_dist = react_dist_dir()
    if (react_dist / "assets").exists():
        api.mount("/assets", StaticFiles(directory=react_dist / "assets"), name="assets")

    @api.get("/sw.js")
    async def service_worker() -> Response:
        path = react_dist / "sw.js"
        if not path.exists():
            return error_response(404, "not_found", "service worker not found")
        return FileResponse(path, media_type="application/javascript")

    @api.get("/", response_class=HTMLResponse)
    async def html():
        index = react_dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return html_page(app)

    @api.get("/healthz")
    async def healthz() -> Response:
        return json_response({"status": "ok"})

    @api.post("/api/auth/login")
    async def auth_login(request: Request) -> Response:
        if deployment.admin_auth.mode != "builtin":
            return error_response(404, "not_found", "builtin auth is not enabled")
        try:
            body = await read_json(request, LoginBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        expected = deployment.admin_auth.builtin.password
        if expected and body.password != expected:
            return error_response(401, "unauthorized", "invalid credentials")
        session_id, csrf_token = sessions.create(user_id="admin", display_name="Admin")
        response = json_response({"csrf_token": csrf_token})
        response.set_cookie(
            deployment.admin_auth.builtin.session_cookie_name,
            session_id,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @api.post("/api/auth/logout")
    async def auth_logout(request: Request) -> Response:
        if deployment.admin_auth.mode != "builtin":
            return error_response(404, "not_found", "builtin auth is not enabled")
        cookie_name = deployment.admin_auth.builtin.session_cookie_name
        session_id = request.cookies.get(cookie_name)
        if session_id is not None:
            sessions.delete(session_id)
        response = json_response({"logged_out": True})
        response.delete_cookie(cookie_name)
        return response

    @api.post("/api/admin/interrupt")
    async def admin_interrupt(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "admin requests require loopback access")
        try:
            raw = await read_json(request, dict[str, object])
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        if raw.get("all") is True:
            return json_response({"interrupted": app.interrupt_all()})
        conversation_id = raw.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            return error_response(400, "bad_request", "conversation_id is required")
        return json_response({"conversation_id": conversation_id, "interrupted": app.interrupt(conversation_id)})

    @api.post("/api/admin/shutdown")
    async def admin_shutdown(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "admin requests require loopback access")
        if on_shutdown is not None:
            on_shutdown()
        return json_response({"status": "shutting_down"})

    @api.get("/api/bootstrap")
    async def api_bootstrap() -> Response:
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/integrations")
    async def api_integrations() -> Response:
        return json_response({"items": await app.integration_snapshots()})

    @api.get("/api/integrations/{integration_type}")
    async def api_integration(integration_type: str) -> Response:
        for integration in await app.integration_snapshots():
            if integration.type == integration_type:
                return json_response(integration)
        return error_response(404, "not_found", "integration type not found")

    @api.get("/api/provider-protocols")
    async def api_provider_protocols() -> Response:
        return json_response({"items": [msgspec.to_builtins(item) for item in app.runtime.provider_registry.protocol_specs()]})

    @api.get("/api/providers")
    async def api_providers() -> Response:
        items: list[ProviderSnapshot] = []
        for record in sorted(app.provider_records.values(), key=lambda item: item.id):
            cards = await app.runtime.state.list_model_cards(record.id)
            items.append(app.provider_snapshot(record, cards))
        return json_response({"items": items})

    @api.get("/api/providers/{provider_id}")
    async def api_provider(provider_id: str) -> Response:
        record = app.provider_records.get(provider_id)
        if record is None:
            return error_response(404, "not_found", "provider not found")
        cards = await app.runtime.state.list_model_cards(provider_id)
        return json_response(app.redacted_provider_detail(record, cards))

    @api.put("/api/providers/{provider_id}")
    async def api_put_provider(provider_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, ProviderInput)
            await app.put_provider(provider_id, body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            if isinstance(exc, ValueError) and "cannot change protocol" in str(exc):
                return error_response(409, "conflict", str(exc))
            return bad_request(exc)
        return json_response(await app.bootstrap_snapshot())

    @api.delete("/api/providers/{provider_id}")
    async def api_delete_provider(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            await app.delete_provider(provider_id)
        except ValueError as exc:
            return error_response(409, "conflict", str(exc))
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/providers/{provider_id}/validate")
    async def api_validate_provider(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            result = await app.validate_provider(provider_id)
        except Exception as exc:
            return error_response(503, "provider_unavailable", str(exc))
        return json_response(msgspec.to_builtins(result))

    @api.get("/api/providers/{provider_id}/balance")
    async def api_provider_balance(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            balance = await app.provider_balance(provider_id)
        except Exception as exc:
            return error_response(503, "provider_unavailable", str(exc))
        if balance is None:
            return json_response({"available": False})
        return json_response(msgspec.to_builtins(balance))

    @api.post("/api/providers/{provider_id}/catalog/refresh")
    async def api_refresh_provider_catalog(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            cards = await app.refresh_provider_catalog(provider_id)
        except Exception as exc:
            return error_response(503, "provider_unavailable", str(exc))
        return json_response({"model_cards": [model_card_wire(card) for card in cards]})

    @api.get("/api/providers/{provider_id}/model-cards")
    async def api_provider_model_cards(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        cards = await app.runtime.state.list_model_cards(provider_id)
        return json_response({"items": [model_card_wire(card) for card in cards]})

    @api.put("/api/providers/{provider_id}/model-cards/{selector}")
    async def api_put_provider_model_card(provider_id: str, selector: str, request: Request) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            body = await read_json(request, ModelCardInput)
            if body.selector != selector:
                raise ValueError("selector in path must match body.selector")
            card = await app.put_model_card(provider_id, body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(model_card_wire(card))

    @api.delete("/api/providers/{provider_id}/model-cards/{selector}")
    async def api_delete_provider_model_card(provider_id: str, selector: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            await app.delete_model_card(provider_id, selector)
        except ValueError as exc:
            return error_response(409, "conflict", str(exc))
        return json_response(await app.bootstrap_snapshot())

    @api.put("/api/actors/{actor_id}")
    async def api_put_actor(actor_id: str, request: Request) -> Response:
        try:
            raw = await read_json(request, dict[str, object])
            if "llm" in raw:
                return error_response(400, "bad_request", "field llm is deprecated; use provider")
            raw["id"] = actor_id
            raw.pop("tools", None)
            record = msgspec.convert(raw, ActorRecord)
            if record.provider not in app.provider_records:
                return error_response(422, "configuration_required", f"unknown provider: {record.provider}")
            card = await app.runtime.state.load_model_card(record.provider, record.model.selector)
            if card is None:
                return error_response(
                    422,
                    "model_selector_not_found",
                    f"model selector not found: {record.model.selector}",
                    detail={"provider_id": record.provider, "selector": record.model.selector},
                )
            if not has_pricing_configured(card):
                return error_response(
                    422,
                    "model_pricing_required",
                    f"model pricing is required before binding an actor: {record.model.selector}",
                    detail={"provider_id": record.provider, "selector": record.model.selector},
                )
            record = ActorRecord(
                id=record.id,
                name=record.name,
                description=record.description,
                workspace=record.workspace,
                persona=record.persona,
                model=ModelCard(
                    selector=card.selector,
                    reasoning_effort=record.model.reasoning_effort.strip(),
                    vision=card.vision,
                    toolcall=card.toolcall,
                    json=card.json,
                    input_price_per_million=card.input_price_per_million,
                    cached_input_price_per_million=card.cached_input_price_per_million,
                    output_price_per_million=card.output_price_per_million,
                ),
                provider=record.provider,
            )
            await app.update_actor(record)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/actors/{actor_id}")
    async def api_actor(actor_id: str) -> Response:
        record = app.actor_records.get(actor_id)
        if record is None:
            return error_response(404, "not_found", "actor not found")
        return json_response(msgspec.to_builtins(record))

    @api.post("/api/actors/{actor_id}/enable")
    async def api_enable_actor(actor_id: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.enable_actor(actor_id)
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/actors/{actor_id}/disable")
    async def api_disable_actor(actor_id: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.disable_actor(actor_id)
        return json_response(await app.bootstrap_snapshot())

    @api.delete("/api/actors/{actor_id}")
    async def api_delete_actor(actor_id: str) -> Response:
        removed = await app.remove_actor(actor_id)
        if not removed:
            return error_response(404, "not_found", "actor not found")
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/actors/{actor_id}/browse")
    async def api_browse_actor(actor_id: str, path: str = "") -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            target = workspace_path(workspace, path)
        except ValueError as exc:
            return bad_request(exc)
        if not target.is_dir():
            return error_response(404, "not_found", "directory not found")
        return json_response(directory_snapshot(workspace, target))

    @api.get("/api/actors/{actor_id}/files/{file_path:path}")
    async def api_actor_file(actor_id: str, file_path: str) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            target = workspace_path(workspace, file_path)
        except ValueError as exc:
            return bad_request(exc)
        if not target.is_file():
            return error_response(404, "not_found", "file not found")
        return Response(content=target.read_bytes(), media_type=mimetypes.guess_type(target)[0] or "application/octet-stream")

    @api.post("/api/actors/{actor_id}/uploads")
    async def api_upload_actor(actor_id: str, file: list[UploadFile] = File(...), path: str | None = None) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            return json_response({"files": await save_uploads(workspace, file, destination=path)})
        except ValueError as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/directories")
    async def api_create_workspace_directory(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceMkdirBody)
            return json_response(make_directory(workspace, body.path), status=201)
        except FileExistsError as exc:
            return error_response(409, "conflict", str(exc))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/rename")
    async def api_rename_workspace_entry(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceRenameBody)
            return json_response(rename_entry(workspace, body.path, body.name))
        except FileExistsError as exc:
            return error_response(409, "conflict", str(exc))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.post("/api/actors/{actor_id}/workspace/move")
    async def api_move_workspace_entries(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceMoveBody)
            return json_response(move_entries(workspace, body.sources, body.destination))
        except FileExistsError as exc:
            return error_response(409, "conflict", str(exc))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.delete("/api/actors/{actor_id}/workspace/entries")
    async def api_delete_workspace_entries(actor_id: str, request: Request) -> Response:
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, WorkspaceDeleteBody)
            return json_response(delete_entries(workspace, body.paths))
        except FileNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)

    @api.put("/api/integrations/{integration_type}/config")
    async def api_configure_integration(integration_type: str, request: Request) -> Response:
        if integration_type not in app.runtime.integration_registry.specs():
            return error_response(404, "not_found", "integration type not found")
        try:
            raw = await read_json(request, dict[str, object])
            name_value = raw.get("name", integration_type)
            config_value = raw.get("config", {})
            if not isinstance(name_value, str) or not isinstance(config_value, dict):
                raise ValueError("name must be a string and config must be an object")
            existing = app.integration_records.get(integration_type)
            await app.configure_integration(
                IntegrationRecord(
                    id=integration_type,
                    type=integration_type,
                    name=name_value,
                    config=merge_redacted_config(dict(config_value), existing.config if existing else None),
                )
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/integrations/{integration_type}/enable")
    async def api_enable_integration(integration_type: str) -> Response:
        try:
            integration = await app.enable_configured_integration(integration_type)
        except (KeyError, msgspec.ValidationError, ValueError) as exc:
            return error_response(422, "configuration_required", str(exc))
        if integration is None:
            return error_response(422, "configuration_required", "integration config is required before enable")
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/integrations/{integration_type}/disable")
    async def api_disable_integration(integration_type: str) -> Response:
        if not await app.disable_integration(integration_type):
            return error_response(404, "not_found", "integration config not found")
        return json_response(await app.bootstrap_snapshot())

    @api.get("/api/conversations/{conversation_id}")
    async def api_conversation(conversation_id: str) -> Response:
        summary = await app.conversation_summary(conversation_id)
        cached = app.runtime.conversations.has(conversation_id)
        if summary is None and not cached:
            return error_response(404, "not_found", "conversation not found")
        payload: dict[str, object] = (
            msgspec.to_builtins(summary)
            if summary is not None
            else {"id": conversation_id, "message_count": 0, "last_seq": None}
        )
        payload["active"] = payload.get("status") == "active" if summary is not None else cached
        payload["history_url"] = f"/api/conversations/{conversation_id}/history"
        return json_response(payload)

    @api.get("/api/conversations/{conversation_id}/history")
    async def api_conversation_history(conversation_id: str) -> Response:
        items = await app.conversation_history(conversation_id)
        if not items and not app.runtime.conversations.has(conversation_id):
            return error_response(404, "not_found", "conversation not found")
        return json_response({"conversation_id": conversation_id, "items": items})

    @api.get("/api/conversations/{conversation_id}/costs")
    async def api_conversation_costs(conversation_id: str) -> Response:
        items = await app.runtime.state.load_costs(conversation_id)
        if not items and await app.conversation_summary(conversation_id) is None:
            return error_response(404, "not_found", "conversation not found")
        return json_response({"conversation_id": conversation_id, "items": items})

    @api.delete("/api/conversations/{conversation_id}")
    async def api_delete_conversation(conversation_id: str) -> Response:
        discarded = await app.runtime.conversations.discard(conversation_id)
        deleted_data = await app.runtime.delete_conversation_data(conversation_id)
        if not discarded and not deleted_data:
            return error_response(404, "not_found", "conversation not found")
        return json_response({"id": conversation_id, "deleted": True})

    @api.get("/api/routes")
    async def api_routes() -> Response:
        return json_response({"items": [msgspec.to_builtins(record) for record in await app.list_routes()]})

    @api.post("/api/routes")
    async def api_create_route(request: Request) -> Response:
        try:
            body = await read_json(request, RouteBody)
            record = body.to_record()
            if body.id and await route_exists(app, body.id):
                return error_response(409, "conflict", f"route already exists: {body.id}")
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        if record.actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.put_route(record)
        return json_response(msgspec.to_builtins(record))

    @api.put("/api/routes/{route_id}")
    async def api_put_route(route_id: str, request: Request) -> Response:
        try:
            raw = await read_json(request, dict[str, object])
            raw["id"] = route_id
            record = msgspec.convert(raw, RouteRecord)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        if record.actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        await app.put_route(record)
        return json_response(msgspec.to_builtins(record))

    @api.delete("/api/routes/{route_id}")
    async def api_delete_route(route_id: str) -> Response:
        if not await app.delete_route(route_id):
            return error_response(404, "not_found", "route not found")
        return json_response({"id": route_id, "deleted": True})

    @api.get("/api/runtime")
    async def api_runtime() -> Response:
        return json_response(app.runtime_snapshot())

    @api.get("/api/tasks")
    async def api_tasks(owner: str | None = None, name_glob: str = "") -> Response:
        records = app.runtime.tasks.list(owner=owner, name_glob=name_glob)
        return json_response({"items": [task_record_snapshot(record) for record in records]})

    @api.get("/api/tasks/{task_id}")
    async def api_task(task_id: str) -> Response:
        if task_id not in app.runtime.tasks:
            return error_response(404, "not_found", "task not found")
        return json_response(task_record_snapshot(app.runtime.tasks.get(task_id), include_stdout=True))

    @api.post("/api/tasks")
    async def api_create_task(request: Request) -> Response:
        if not client_is_loopback(request):
            return error_response(401, "unauthorized", "task submit requires loopback access")
        try:
            body = await read_json(request, SubmitTaskBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        actor_id = body.owner.split(":conv:", 1)[0].removeprefix("actor:")
        workspace = actor_workspace(app, actor_id)
        if workspace is None:
            return error_response(404, "not_found", "actor not found")
        snapshot = await app.submit_shell_task(
            name=body.name,
            shell=body.shell,
            intro=body.intro,
            owner=body.owner,
            workspace=workspace,
            wait_s=body.wait_s,
        )
        return json_response(snapshot)

    @api.post("/api/tasks/{task_id}/cancel")
    async def api_cancel_task(task_id: str) -> Response:
        if task_id not in app.runtime.tasks:
            return error_response(404, "not_found", "task not found")
        app.runtime.cancel_runtime_task(task_id)
        return json_response(task_record_snapshot(app.runtime.tasks.get(task_id), include_stdout=True))

    @api.get("/api/cron-jobs")
    async def api_cron_jobs(owner: str | None = None, status: str | None = None, name_glob: str = "") -> Response:
        items = await list_cron_jobs(app.runtime, owner=owner, status=status, name_glob=name_glob)
        return json_response({"items": items})

    @api.get("/api/cron-jobs/{job_id}")
    async def api_cron_job(job_id: str) -> Response:
        try:
            return json_response(await get_cron_job(app.runtime, job_id))
        except KeyError:
            return error_response(404, "not_found", "cron job not found")

    @api.post("/api/cron-jobs")
    async def api_create_cron_job(request: Request) -> Response:
        try:
            body = await read_json(request, CreateCronJobBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        from ...runtime.cron import CronSchedule, CronScheduleError, decode_cron_action

        try:
            schedule = msgspec.convert(body.schedule, CronSchedule)
            action = decode_cron_action(body.action)
            snapshot = await create_cron_job(
                app.runtime,
                owner=body.owner,
                name=body.name,
                schedule=schedule,
                action=action,
                once=body.once,
            )
        except (msgspec.ValidationError, TypeError, ValueError, CronScheduleError) as exc:
            return bad_request(exc)
        return json_response(snapshot, status=201)

    @api.post("/api/cron-jobs/{job_id}/pause")
    async def api_pause_cron_job(job_id: str) -> Response:
        try:
            return json_response(await pause_cron_job(app.runtime, job_id))
        except KeyError:
            return error_response(404, "not_found", "cron job not found")

    @api.post("/api/cron-jobs/{job_id}/resume")
    async def api_resume_cron_job(job_id: str) -> Response:
        try:
            return json_response(await resume_cron_job(app.runtime, job_id))
        except KeyError:
            return error_response(404, "not_found", "cron job not found")

    @api.delete("/api/cron-jobs/{job_id}")
    async def api_delete_cron_job(job_id: str) -> Response:
        deleted = await delete_cron_job(app.runtime, job_id)
        if not deleted:
            return error_response(404, "not_found", "cron job not found")
        return json_response({"id": job_id, "deleted": True})

    @api.get("/api/notifications/vapid-public-key")
    async def api_vapid_public_key() -> Response:
        return json_response({"public_key": vapid_public_key_for(app.runtime)})

    @api.post("/api/notifications/subscriptions")
    async def api_create_push_subscription(request: Request) -> Response:
        try:
            body = await read_json(request, PushSubscriptionBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        snapshot = await save_push_subscription(app.runtime, endpoint=body.endpoint, keys=body.keys)
        return json_response(snapshot, status=201)

    @api.delete("/api/notifications/subscriptions/{subscription_id}")
    async def api_delete_push_subscription(subscription_id: str) -> Response:
        deleted = await app.runtime.push_subscriptions.delete(subscription_id)
        if not deleted:
            return error_response(404, "not_found", "subscription not found")
        return json_response({"id": subscription_id, "deleted": True})

    @api.post("/api/shares")
    async def api_create_share(request: Request) -> Response:
        try:
            body = await read_json(request, PublishShareBody)
            grant = await app.runtime.shares.publish(
                actor_id=body.actor_id,
                source_path=body.source_path,
                expires_at=body.expires_at,
            )
        except ShareNotFoundError as exc:
            return error_response(404, "not_found", str(exc))
        except ShareBadRequestError as exc:
            return bad_request(exc)
        except (SharePublishError, OSError) as exc:
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, development=app.runtime.development),
                detail=internal_error_detail(exc, development=app.runtime.development),
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(share_grant_snapshot(grant, public_url_base=deployment.public_url_base), status=201)

    @api.get("/api/shares")
    async def api_shares() -> Response:
        items = [
            share_grant_snapshot(grant, public_url_base=deployment.public_url_base)
            for grant in app.runtime.shares.list_grants()
        ]
        return json_response({"items": items})

    @api.get("/api/shares/{share_id}")
    async def api_share(share_id: str) -> Response:
        try:
            grant = app.runtime.shares.get(share_id)
        except ShareNotFoundError:
            return error_response(404, "not_found", "share not found")
        return json_response(share_grant_snapshot(grant, public_url_base=deployment.public_url_base))

    @api.delete("/api/shares/{share_id}")
    async def api_revoke_share(share_id: str) -> Response:
        try:
            grant = await app.runtime.shares.revoke(share_id)
        except ShareNotFoundError:
            return error_response(404, "not_found", "share not found")
        return json_response({"id": grant.id, "revoked": grant.revoked})

    @api.post("/api/actors/{actor_id}/inbound")
    async def api_actor_inbound(actor_id: str, request: Request) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, ActorMessage)
            if not body.text:
                return error_response(400, "bad_request", "text is required")
            result = await app.deliver_actor_inbound(actor_id, body)
        except MailboxUnavailableError as exc:
            return error_response(
                500,
                "internal_error",
                internal_error_message(exc, development=app.runtime.development),
                detail=internal_error_detail(exc, development=app.runtime.development),
            )
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(result)

    @api.get("/api/actors/{actor_id}/kv/{key:path}")
    async def api_kv_get(actor_id: str, key: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            document = await app.runtime.kv.get(actor_id, key)
        except KvBadRequestError as exc:
            return bad_request(exc)
        if document is None:
            return error_response(404, "not_found", "key not found")
        response = json_response(document_snapshot(document))
        response.headers["ETag"] = f'"{document.etag}"'
        return response

    @api.put("/api/actors/{actor_id}/kv/{key:path}")
    async def api_kv_put(actor_id: str, key: str, request: Request) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            body = await read_json(request, KvPutBody)
            document = await app.runtime.kv.put(
                actor_id,
                key,
                body.value,
                if_match=parse_if_match(request.headers.get("if-match")),
            )
        except KvConflictError as exc:
            return error_response(409, "conflict", str(exc), detail={"reason": exc.reason})
        except KvBadRequestError as exc:
            return bad_request(exc)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        response = json_response(document_snapshot(document))
        response.headers["ETag"] = f'"{document.etag}"'
        return response

    @api.delete("/api/actors/{actor_id}/kv/{key:path}")
    async def api_kv_delete(actor_id: str, key: str) -> Response:
        if actor_id not in app.actor_records:
            return error_response(404, "not_found", "actor not found")
        try:
            deleted = await app.runtime.kv.delete(actor_id, key)
        except KvBadRequestError as exc:
            return bad_request(exc)
        if not deleted:
            return error_response(404, "not_found", "key not found")
        return json_response({"actor_id": actor_id, "key": normalize_key(key), "deleted": True})

    @api.websocket("/api/ws")
    async def websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        connection_tasks: set[asyncio.Task[None]] = set()
        send_lock = asyncio.Lock()

        async def send(payload: dict[str, object]) -> None:
            async with send_lock:
                await websocket.send_text(msgspec.json.encode(payload).decode("utf-8"))

        ws_listener = WsListener(send)
        app.runtime.listeners.add(ws_listener)

        def track_task(task: asyncio.Task[None]) -> None:
            if task.get_name() == "conversation_send":
                return
            connection_tasks.add(task)
            task.add_done_callback(connection_tasks.discard)

        try:
            while True:
                raw = await websocket.receive_text()
                task = await handle_ws_command(app, raw, send, ws_listener)
                if task is not None:
                    track_task(task)
        except WebSocketDisconnect:
            pass
        finally:
            ws_listener.close()
            app.runtime.listeners.remove(ws_listener)
            for task in connection_tasks:
                task.cancel()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

    @api.get("/{path:path}", response_class=HTMLResponse)
    async def react_app(path: str):
        if path.startswith("api/"):
            return error_response(404, "not_found", "API endpoint not found")
        index = react_dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return html_page(app)

    return api
