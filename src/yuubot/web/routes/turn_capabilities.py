"""Loopback-only fixer and web-search capabilities bound to a live turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...integrations.web import WebIntegration
from ...llm.gateway import GatewayClient, GatewayError, HostedSearchResult, RequestMetadata
from ...runtime.event_payloads import ConversationUsagePayload
from ...runtime.streams import TextStream
from ...runtime.tasks import RuntimeTaskRecord, make_owner, new_task_id
from ...runtime.turn_limits import TurnIdentity, TurnLimitError
from ..request import bad_request, read_json
from ..responses import error_response, json_response

_log = logging.getLogger(__name__)
FIXER_SYNC_WAIT_S = 30.0


class FixerBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    prompt: str
    enable_web_search: bool = False
    pass_through_options: dict[str, object] | None = None


class WebSearchBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    query: str
    max_results: int = 5


def register_turn_capability_routes(
    api: FastAPI,
    app: Yuubot,
    client_is_loopback: Callable[[Request], bool],
) -> None:
    @api.post("/api/fixer/{facade}")
    async def api_fixer(facade: str, request: Request) -> Response:
        identity, error = _turn_identity(app, request, client_is_loopback)
        if error is not None:
            return error
        if facade not in {"gemini", "grok"}:
            return error_response(404, "not_found", "fixer facade not found")
        try:
            body = await read_json(request, FixerBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        prompt = body.prompt.strip()
        if not prompt:
            return error_response(400, "bad_request", "prompt must not be empty")
        if len(prompt) > 20_000:
            return error_response(400, "bad_request", "prompt must be at most 20000 characters")
        assert identity is not None
        status = app.gateway_status_snapshot()
        enabled = status.fixer_gemini_enabled if facade == "gemini" else status.fixer_grok_enabled
        if not enabled:
            return error_response(409, "hosted_search_unavailable", f"ask-{facade} hosted search is unavailable")
        client = app.runtime.gateway_client
        if not isinstance(client, GatewayClient):
            return error_response(409, "hosted_search_unavailable", "hosted search requires a configured Gateway")

        token = request.headers.get("X-Yuubot-Turn-Token", "")
        try:
            reservation = await app.runtime.turn_limits.reserve(token, f"fixer_{facade}")
        except TurnLimitError as exc:
            return error_response(429, exc.code, str(exc))

        owner = _fixer_task_owner(app, identity)
        record = RuntimeTaskRecord(
            new_task_id(),
            owner,
            "fixer",
            f"fixer-{facade}",
            f"{facade} fixer answer",
            interactive=False,
            delivery="conversation",
            delivery_state="held",
            metadata={"facade": facade, "turn_id": identity.turn_id},
        )

        async def operation(_stdin: TextStream, stdout: TextStream) -> dict[str, object]:
            try:
                try:
                    result = await client.hosted_search(
                        f"ask-{facade}",
                        prompt,
                        RequestMetadata(
                            identity.trace_id,
                            identity.actor_id,
                            identity.conversation_id,
                            "fixer",
                        ).to_dict(),
                        body.enable_web_search,
                        body.pass_through_options,
                    )
                except GatewayError as exc:
                    record.metadata["error_code"] = exc.code
                    raise
                await reservation.commit()
                await _record_fixer_usage(app, identity, facade, result)
                payload = {
                    "text": result.text,
                    "citations": msgspec.to_builtins(result.citations),
                }
                stdout.write(_format_fixer_output(result))
                return payload
            finally:
                await reservation.release()

        app.runtime.tasks.put(record)
        app.runtime.scheduler.schedule(record, operation)
        _log.info(
            "fixer task registered task_id=%s facade=%s owner=%s sync_wait_s=%s",
            record.id,
            facade,
            owner,
            FIXER_SYNC_WAIT_S,
        )
        try:
            await app.runtime.wait_until_terminal_or_timeout(record.id, FIXER_SYNC_WAIT_S)
        except asyncio.CancelledError:
            record.release_held_delivery(True)
            _log.info("fixer task detached after caller cancellation task_id=%s facade=%s", record.id, facade)
            raise

        if record.is_terminal():
            record.release_held_delivery(False)
            app.runtime.tasks.refresh_terminal_retention(record)
            if record.status == "done" and isinstance(record.result, dict):
                _log.info("fixer task completed synchronously task_id=%s facade=%s", record.id, facade)
                return json_response({"status": "done", "task_id": record.id, **record.result})
            code = str(record.metadata.get("error_code", "capability_request_failed"))
            return error_response(502, code, record.error or "capability request failed")

        record.release_held_delivery(True)
        _log.info("fixer task auto-detached task_id=%s facade=%s", record.id, facade)
        return json_response({"status": "pending", "task_id": record.id})

    @api.post("/api/web/search")
    async def api_web_search(request: Request) -> Response:
        identity, error = _turn_identity(app, request, client_is_loopback)
        if error is not None:
            return error
        try:
            body = await read_json(request, WebSearchBody)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return bad_request(exc)
        query = body.query.strip()
        if not query:
            return error_response(400, "bad_request", "query must not be empty")
        if body.max_results < 1 or body.max_results > 20:
            return error_response(400, "bad_request", "max_results must be between 1 and 20")
        web = next(
            (integration for integration in app.runtime.integrations.values() if isinstance(integration, WebIntegration)),
            None,
        )
        if web is None:
            return error_response(409, "web_search_unavailable", "yext.web integration is not enabled")

        async def operation() -> object:
            from yext.web import _search_direct

            return await _search_direct(
                query,
                body.max_results,
                web.config.tavily_api_key,
                web.config.tavily_base_url,
                web.config.timeout_s,
            )

        result, limit_error = await _run_limited(app, request, "web_search", operation)
        if limit_error is not None:
            return limit_error
        return json_response({"items": result})


def _fixer_task_owner(app: Yuubot, identity: TurnIdentity) -> str:
    if identity.conversation_id.startswith("subagent:"):
        task_id = identity.conversation_id.removeprefix("subagent:")
        if task_id in app.runtime.tasks:
            return app.runtime.tasks.get(task_id).owner
    return make_owner(identity.actor_id, identity.conversation_id)


async def _record_fixer_usage(
    app: Yuubot,
    identity: TurnIdentity,
    facade: str,
    result: HostedSearchResult,
) -> None:
    usage = result.usage
    account = {**result.account, "purpose": "fixer", "facade": facade}
    await app.runtime.state.append_usage(identity.conversation_id, usage, account)
    app.runtime.emit(
        ConversationUsagePayload(
            identity.conversation_id,
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.cache_write_tokens,
            usage.output_tokens,
            account,
        )
    )


def _format_fixer_output(result: HostedSearchResult) -> str:
    lines = [result.text]
    if result.citations:
        lines.append("Citations:")
        for citation in result.citations:
            label = f"{citation.title}: " if citation.title else ""
            lines.append(f"- {label}{citation.url}")
    return "\n".join(lines)


def _turn_identity(
    app: Yuubot,
    request: Request,
    client_is_loopback: Callable[[Request], bool],
) -> tuple[TurnIdentity | None, Response | None]:
    if not client_is_loopback(request):
        return None, error_response(401, "unauthorized", "turn capability requires loopback access")
    token = request.headers.get("X-Yuubot-Turn-Token", "")
    try:
        identity = app.runtime.turn_limits.identity(token)
    except TurnLimitError as exc:
        return None, error_response(401, exc.code, str(exc))
    actor = app.runtime.actors.get(identity.actor_id)
    conversation = app.runtime.conversations.get_if_present(identity.conversation_id)
    subagent_task_id = identity.conversation_id.removeprefix("subagent:")
    subagent_active = (
        identity.conversation_id.startswith("subagent:")
        and subagent_task_id in app.runtime.tasks
        and app.runtime.tasks.get(subagent_task_id).kind == "agent"
        and app.runtime.tasks.get(subagent_task_id).status in {"running", "waiting_children"}
    )
    if actor is None or (not subagent_active and (conversation is None or not conversation.running)):
        return None, error_response(409, "turn_context_invalid", "turn is no longer active")
    if conversation is not None and conversation.context.actor != identity.actor_id:
        return None, error_response(403, "turn_context_invalid", "turn does not own this conversation")
    return identity, None


async def _run_limited(
    app: Yuubot,
    request: Request,
    capability: str,
    operation: Callable[[], Awaitable[object]],
) -> tuple[object | None, Response | None]:
    token = request.headers.get("X-Yuubot-Turn-Token", "")
    try:
        return await app.runtime.turn_limits.run(token, capability, operation), None
    except TurnLimitError as exc:
        return None, error_response(429, exc.code, str(exc))
    except GatewayError as exc:
        return None, error_response(502, exc.code, str(exc))
    except Exception:
        return None, error_response(502, "capability_request_failed", "capability request failed")
