"""Loopback-only fixer and web-search capabilities bound to a live turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...integrations.web import WebIntegration
from ...llm.gateway import GatewayClient, GatewayError, HostedSearchResult, RequestMetadata
from ...runtime.event_payloads import ConversationUsagePayload
from ...runtime.turn_limits import TurnIdentity, TurnLimitError
from ..request import bad_request, read_json
from ..responses import error_response, json_response


class FixerBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    prompt: str


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

        async def operation() -> object:
            return await client.hosted_search(
                f"ask-{facade}",
                prompt,
                RequestMetadata(
                    identity.trace_id,
                    identity.actor_id,
                    identity.conversation_id,
                    "fixer",
                ).to_dict(),
            )

        result, limit_error = await _run_limited(
            app,
            request,
            f"fixer_{facade}",
            operation,
        )
        if limit_error is not None:
            return limit_error
        result = cast(HostedSearchResult, result)
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
        return json_response({"text": result.text, "citations": result.citations})

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
        and app.runtime.tasks.get(subagent_task_id).status == "running"
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
