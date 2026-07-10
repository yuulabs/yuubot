"""OpenAI-compatible Endpoint clients and ordered Alias routing."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, cast
from urllib.parse import urlparse

import msgspec
from attrs import define, field
from openai import AsyncOpenAI, AsyncStream, BadRequestError
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from ..domain.messages import (
    ContentItem,
    ConversationContext,
    GenReasoning,
    GenText,
    GenToolCall,
    HistoryItem,
    InputMessage,
    LLMInput,
    ToolResult,
)
from ..domain.stream import (
    ReasoningDeltaPayload,
    StopReason,
    StreamEvent,
    StreamStopPayload,
    TextDeltaPayload,
    ToolArgumentsDeltaPayload,
    ToolNamePayload,
    Usage,
)
from ..runtime.cache import CachePool
from ..domain.models import AliasModelSelector, ExactModelSelector, ModelSelector
from ..util.paths import safe_workspace_path
from ..util.stream import stream_stop_event

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class EndpointRecord(msgspec.Struct, frozen=True):
    id: str
    name: str
    base_url: str = ""
    connect_timeout_s: float = 10
    request_timeout_s: float = 300
    models: list[str] = msgspec.field(default_factory=list)
    checked_at: str = ""
    last_error: str | None = None


class EndpointInput(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: str
    base_url: str
    api_key: str = ""
    clear_api_key: bool = False
    connect_timeout_s: float = 10
    request_timeout_s: float = 300
    refresh_models: bool = True


def endpoint_record_from_input(endpoint_id: str, value: EndpointInput) -> EndpointRecord:
    config = EndpointRecord(
        endpoint_id.strip(),
        value.name.strip(),
        value.base_url.strip().rstrip("/"),
        value.connect_timeout_s,
        value.request_timeout_s,
    )
    validate_endpoint(config)
    return config


def validate_endpoint(config: EndpointRecord) -> None:
    if not config.id:
        raise ValueError("endpoint id is required")
    if not config.name:
        raise ValueError("endpoint name is required")
    if config.connect_timeout_s <= 0 or config.request_timeout_s <= 0:
        raise ValueError("endpoint timeouts must be greater than zero")
    if not config.base_url:
        raise ValueError("endpoint base_url is required")
    parsed = urlparse(config.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint base_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint base_url must not contain credentials")


class AliasTarget(msgspec.Struct, frozen=True):
    endpoint_id: str
    model: str


InputModality: TypeAlias = Literal["text", "image", "audio", "video"]


class AliasRecord(msgspec.Struct, frozen=True):
    id: str
    modalities: list[InputModality] = msgspec.field(default_factory=lambda: ["text"])
    targets: list[AliasTarget] = msgspec.field(default_factory=list)


class AliasInput(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    modalities: list[InputModality] = msgspec.field(default_factory=lambda: ["text"])
    targets: list[AliasTarget] = msgspec.field(default_factory=list)


def alias_record_from_input(alias_id: str, value: AliasInput) -> AliasRecord:
    record = AliasRecord(alias_id.strip(), list(dict.fromkeys(value.modalities)), value.targets)
    validate_alias(record)
    return record


def validate_alias(alias: AliasRecord) -> None:
    if not alias.id:
        raise ValueError("alias id is required")
    if "text" not in alias.modalities:
        raise ValueError("alias modalities must include text")
    if not alias.targets:
        raise ValueError("alias must contain at least one target")
    for target in alias.targets:
        if not target.endpoint_id.strip() or not target.model.strip():
            raise ValueError("alias targets require endpoint_id and model")


# ---------------------------------------------------------------------------
# Status types
# ---------------------------------------------------------------------------


class EndpointStatus(msgspec.Struct, frozen=True):
    id: str
    name: str
    base_url: str
    connected: bool
    models: list[str] = msgspec.field(default_factory=list)
    checked_at: str = ""
    last_error: str | None = None
    has_api_key: bool = False
    connect_timeout_s: float = 10
    request_timeout_s: float = 300


class GatewayStatus(msgspec.Struct, frozen=True):
    endpoints: list[EndpointStatus] = msgspec.field(default_factory=list)
    aliases: list[AliasRecord] = msgspec.field(default_factory=list)
    fixer_gemini_enabled: bool = False
    fixer_grok_enabled: bool = False
    fast_delegate_enabled: bool = False
    intelligent_delegate_enabled: bool = False


class HostedSearchCitation(msgspec.Struct, frozen=True):
    url: str
    title: str = ""


class HostedSearchResult(msgspec.Struct, frozen=True):
    text: str
    citations: list[HostedSearchCitation]
    usage: Usage = msgspec.field(default_factory=Usage)
    account: dict[str, object] = msgspec.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

GatewayErrorCode = Literal[
    "gateway_auth_failed",
    "gateway_model_unavailable",
    "gateway_temporarily_unavailable",
    "gateway_unreachable",
    "hosted_search_unavailable",
    "gateway_request_failed",
    "gateway_modality_unavailable",
]


class GatewayError(Exception):
    """Mapped gateway error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _map_openai_error(exc: Exception) -> GatewayError:
    """Map an openai SDK exception to a stable GatewayError code."""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        ConflictError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        APIStatusError,
    )

    if isinstance(exc, AuthenticationError | PermissionDeniedError):
        return GatewayError("gateway_auth_failed", "gateway authentication failed")
    if isinstance(exc, NotFoundError):
        return GatewayError("gateway_model_unavailable", "model unavailable")
    if isinstance(exc, BadRequestError):
        detail = str(exc).lower()
        if "model" in detail and any(
            marker in detail
            for marker in ("unavailable", "not found", "does not exist", "invalid")
        ):
            return GatewayError("gateway_model_unavailable", "model unavailable")
        return GatewayError("gateway_request_failed", "gateway request failed")
    if isinstance(exc, RateLimitError | ConflictError):
        return GatewayError(
            "gateway_temporarily_unavailable", "gateway temporarily unavailable"
        )
    if isinstance(exc, APITimeoutError | APIConnectionError):
        return GatewayError("gateway_unreachable", "gateway unreachable")
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return GatewayError(
            "gateway_temporarily_unavailable", "gateway temporarily unavailable"
        )
    return GatewayError("gateway_request_failed", "gateway request failed")


def _is_bad_request_error(exc: Exception) -> bool:
    return isinstance(exc, BadRequestError)


def _request_debug_summary(
    messages: list[ChatCompletionMessageParam],
    tool_specs: list[dict[str, object]],
) -> dict[str, object]:
    """Return safe request shape diagnostics without prompt or secret contents."""
    return {
        "message_count": len(messages),
        "messages": [
            {
                "role": message.get("role"),
                "content_type": type(message.get("content")).__name__,
                "content_length": len(message.get("content") or "")
                if isinstance(message.get("content"), str)
                else None,
                "tool_call_count": len(message.get("tool_calls") or [])
                if isinstance(message.get("tool_calls"), list)
                else 0,
            }
            for message in messages
        ],
        "tool_count": len(tool_specs),
        "tool_names": [
            str((tool.get("function") or {}).get("name", ""))
            for tool in tool_specs
        ],
    }


# ---------------------------------------------------------------------------
# Stream client protocol
# ---------------------------------------------------------------------------


class StreamClient(Protocol):
    """Minimal streaming interface shared by GatewayClient and test doubles."""

    async def stream(
        self,
        input: LLMInput,
        model: ModelSelector | str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent("", "text_delta", TextDeltaPayload())

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Request metadata
# ---------------------------------------------------------------------------


class RequestMetadata(msgspec.Struct, frozen=True):
    """Metadata propagated to the Gateway for tracing and attribution."""

    trace_id: str = ""
    actor_id: str = ""
    conversation_id: str = ""
    purpose: Literal["chat", "fixer", "delegate"] = "chat"
    task_id: str = ""
    parent_conversation_id: str = ""
    subagent: str = ""

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {
            "trace_id": self.trace_id,
            "actor_id": self.actor_id,
            "conversation_id": self.conversation_id,
            "purpose": self.purpose,
        }
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.parent_conversation_id:
            payload["parent_conversation_id"] = self.parent_conversation_id
        if self.subagent:
            payload["subagent"] = self.subagent
        return payload


# ---------------------------------------------------------------------------
# Tool stream state (reused from openai.py)
# ---------------------------------------------------------------------------


@define
class ToolStreamState:
    seen: set[int] = field(factory=set)
    named: set[int] = field(factory=set)
    ids: dict[int, str] = field(factory=dict)
    pending_arguments: dict[int, list[str]] = field(factory=dict)


@define(frozen=True)
class _CachedImageDataUrl:
    value: str

    def get_cache_size(self) -> int:
        return len(self.value)


# ---------------------------------------------------------------------------
# EndpointClient
# ---------------------------------------------------------------------------


@define
class EndpointClient:
    """One standard OpenAI-compatible Endpoint connection."""

    config: EndpointRecord
    _api_key: str = ""
    _client: AsyncOpenAI | None = field(default=None, init=False)
    _status: EndpointStatus = field(init=False)

    def __attrs_post_init__(self) -> None:
        self._status = EndpointStatus(
            self.config.id,
            self.config.name,
            self.config.base_url,
            False,
            self.config.models,
            self.config.checked_at,
            self.config.last_error,
            bool(self._api_key),
            self.config.connect_timeout_s,
            self.config.request_timeout_s,
        )

    @property
    def status(self) -> EndpointStatus:
        return self._status

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def _sdk_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self.config.base_url:
                raise GatewayError(
                    "gateway_unreachable", "gateway base_url not configured"
                )
            import httpx

            self._client = AsyncOpenAI(
                api_key=self._api_key or "unset",
                base_url=self.config.base_url.rstrip("/"),
                timeout=httpx.Timeout(
                    self.config.request_timeout_s,
                    connect=self.config.connect_timeout_s,
                ),
            )
        return self._client

    # -- streaming ----------------------------------------------------------

    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        started = time.perf_counter()
        usage = Usage()
        if stop_event.is_set():
            yield stream_stop_event(
                "interrupted", usage, _with_gateway_latency({}, started)
            )
            return
        try:
            upstream = await self._completion_stream(
                input, model, context, cache, metadata
            )
        except GatewayError:
            raise
        except Exception as exc:
            raise _map_openai_error(exc) from exc

        tool_state = ToolStreamState()
        finish_reason: StopReason = "stop"
        account: dict[str, object] = {}
        try:
            async for chunk in upstream:
                if stop_event.is_set():
                    yield stream_stop_event(
                        "interrupted",
                        usage,
                        _with_gateway_latency(account, started),
                    )
                    return
                for event in _events_from_chunk(chunk, tool_state):
                    yield event
                if chunk.usage is not None:
                    usage = _usage_from_chunk(chunk.usage)
                account.update(_account_from_chunk(chunk))
                for choice in chunk.choices:
                    if choice.finish_reason is not None:
                        finish_reason = cast(StopReason, choice.finish_reason)
        except Exception as exc:
            raise _map_openai_error(exc) from exc
        finally:
            await upstream.close()

        for index in sorted(tool_state.named):
            yield StreamEvent(f"tool-{index}", "tool_arguments_end")

        yield stream_stop_event(
            finish_reason,
            usage,
            _with_gateway_latency(account, started),
        )

    async def _completion_stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        metadata: dict[str, str] | None,
    ) -> AsyncStream[ChatCompletionChunk]:
        options: dict[str, Any] = {"stream_options": {"include_usage": True}}
        messages = _messages(input.messages, context.workspace, cache)
        if input.tool_specs:
            options["tools"] = cast(list[ChatCompletionToolParam], input.tool_specs)
        if metadata:
            options["extra_body"] = {"metadata": metadata}
        try:
            completion = await self._sdk_client().chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **options,
            )
        except Exception as exc:
            if _is_bad_request_error(exc):
                _log.error(
                    "gateway completion rejected endpoint=%s model=%s status=%s "
                    "error=%s request=%s",
                    self.config.id,
                    model,
                    getattr(exc, "status_code", None),
                    getattr(exc, "body", None),
                    _request_debug_summary(messages, input.tool_specs),
                )
            raise
        return completion

    async def hosted_search(
        self,
        model: str,
        prompt: str,
        metadata: dict[str, str],
    ) -> HostedSearchResult:
        started = time.perf_counter()
        try:
            response = await self._sdk_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _FIXER_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                web_search_options={"search_context_size": "medium"},
                extra_body={"metadata": metadata},
            )
        except Exception as exc:
            raise _map_openai_error(exc) from exc
        raw = response.model_dump(mode="json")
        citations = _normalize_citations(raw)
        if not citations:
            raise GatewayError(
                "hosted_search_unavailable",
                "hosted search returned no verifiable citations",
            )
        text = response.choices[0].message.content if response.choices else ""
        if not isinstance(text, str) or not text.strip():
            raise GatewayError("gateway_request_failed", "gateway returned an empty answer")
        usage = _usage_from_chunk(response.usage) if response.usage is not None else Usage()
        account: dict[str, object] = {
            "model": response.model,
            "response_id": response.id,
            "gateway_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        for source, target in (("deployment", "deployment"), ("deployment_id", "deployment")):
            value = _extra_value(response, source)
            if isinstance(value, str) and value:
                account[target] = value
        return HostedSearchResult(text.strip(), citations, usage, account)

    # -- probe --------------------------------------------------------------

    async def probe(self) -> EndpointStatus:
        """Refresh the standard ``/v1/models`` catalog."""
        from ..util.time import utc_now_iso

        if not self.config.base_url:
            self._status = msgspec.structs.replace(
                self._status,
                connected=False,
                checked_at=utc_now_iso(),
                last_error="endpoint base_url not configured",
            )
            return self._status

        try:
            models = await self._probe_models()
        except Exception as exc:
            mapped = _map_openai_error(exc)
            self._status = msgspec.structs.replace(
                self._status,
                connected=False,
                checked_at=utc_now_iso(),
                last_error=mapped.code,
            )
            return self._status

        self._status = EndpointStatus(
            self.config.id,
            self.config.name,
            self.config.base_url,
            connected=True,
            models=models,
            checked_at=utc_now_iso(),
            has_api_key=bool(self._api_key),
            connect_timeout_s=self.config.connect_timeout_s,
            request_timeout_s=self.config.request_timeout_s,
        )
        return self._status

    async def _probe_models(self) -> list[str]:
        page = await self._sdk_client().models.list()
        return sorted({model.id for model in page.data})

    async def _probe_hosted_search(self, model: str) -> bool:
        """Verify that a hosted-search alias returns structured source data."""
        try:
            response = await self._sdk_client().chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": "What is the OpenAI API documentation URL? Reply briefly.",
                    }
                ],
                web_search_options={"search_context_size": "low"},
            )
        except Exception:
            _log.debug("hosted search probe failed model=%s", model, exc_info=True)
            return False
        return _contains_verifiable_source(response.model_dump(mode="json"))

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


_FALLBACK_ERROR_CODES = {
    "gateway_auth_failed",
    "gateway_model_unavailable",
    "gateway_temporarily_unavailable",
    "gateway_unreachable",
}


@define
class GatewayClient:
    """Resolve Actor selectors and route turns across Endpoint clients."""

    endpoints: dict[str, EndpointClient] = field(factory=dict)
    aliases: dict[str, AliasRecord] = field(factory=dict)
    _search_enabled: set[str] = field(factory=set)

    @property
    def status(self) -> GatewayStatus:
        text_aliases = {
            alias.id for alias in self.aliases.values() if "text" in alias.modalities
        }
        return GatewayStatus(
            endpoints=[client.status for client in self.endpoints.values()],
            aliases=list(self.aliases.values()),
            fixer_gemini_enabled="ask-gemini" in self._search_enabled,
            fixer_grok_enabled="ask-grok" in self._search_enabled,
            fast_delegate_enabled="fast" in text_aliases,
            intelligent_delegate_enabled="intelligent" in text_aliases,
        )

    async def stream(
        self,
        input: LLMInput,
        model: ModelSelector | str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        targets = self._targets(model, _input_modalities(input))
        attempted: list[str] = []
        last_error: GatewayError | None = None
        for target in targets:
            target_name = f"{target.endpoint_id}/{target.model}"
            attempted.append(target_name)
            client = self.endpoints.get(target.endpoint_id)
            if client is None:
                last_error = GatewayError(
                    "gateway_model_unavailable",
                    f'endpoint "{target.endpoint_id}" is unavailable',
                )
                continue
            emitted = False
            try:
                async for event in client.stream(
                    input,
                    target.model,
                    context,
                    cache,
                    stop_event,
                    metadata,
                ):
                    emitted = True
                    if event.kind == "stream_stop":
                        payload = event.payload
                        if not isinstance(payload, StreamStopPayload):
                            raise TypeError("stream_stop event requires StreamStopPayload")
                        account = {
                            **payload.account,
                            "endpoint_id": target.endpoint_id,
                            "model": target.model,
                            "fallback_path": list(attempted),
                        }
                        event = StreamEvent(
                            event.group_id,
                            event.kind,
                            msgspec.structs.replace(payload, account=account),
                        )
                    yield event
                return
            except GatewayError as exc:
                if emitted or exc.code not in _FALLBACK_ERROR_CODES:
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
        raise GatewayError("gateway_model_unavailable", "no Gateway target is available")

    def _targets(
        self,
        selector: ModelSelector | str,
        modalities: set[InputModality],
    ) -> list[AliasTarget]:
        if isinstance(selector, ExactModelSelector):
            return [AliasTarget(selector.endpoint_id, selector.model)]
        if not isinstance(selector, AliasModelSelector):
            raise GatewayError("gateway_model_unavailable", "invalid model selector")
        alias_id = selector.alias
        alias = self.aliases.get(alias_id)
        if alias is None:
            raise GatewayError(
                "gateway_model_unavailable", f'alias "{alias_id}" is unavailable'
            )
        unavailable = modalities.difference(alias.modalities)
        if unavailable:
            names = ", ".join(sorted(unavailable))
            raise GatewayError(
                "gateway_modality_unavailable",
                f'alias "{alias_id}" does not accept: {names}',
            )
        return alias.targets

    async def hosted_search(
        self,
        alias_id: str,
        prompt: str,
        metadata: dict[str, str],
    ) -> HostedSearchResult:
        alias = self.aliases.get(alias_id)
        if alias is None:
            raise GatewayError("hosted_search_unavailable", "search alias is unavailable")
        attempted: list[str] = []
        last_error: GatewayError | None = None
        for target in alias.targets:
            target_name = f"{target.endpoint_id}/{target.model}"
            attempted.append(target_name)
            client = self.endpoints.get(target.endpoint_id)
            if client is None:
                continue
            try:
                result = await client.hosted_search(target.model, prompt, metadata)
            except GatewayError as exc:
                if exc.code not in _FALLBACK_ERROR_CODES:
                    raise
                last_error = exc
                continue
            return msgspec.structs.replace(
                result,
                account={
                    **result.account,
                    "endpoint_id": target.endpoint_id,
                    "model": target.model,
                    "fallback_path": attempted,
                },
            )
        if last_error is not None:
            raise last_error
        raise GatewayError("hosted_search_unavailable", "search alias has no endpoint")

    async def probe_hosted_search(self, alias_id: str) -> bool:
        try:
            await self.hosted_search(
                alias_id,
                "What is the OpenAI API documentation URL? Reply briefly.",
                {},
            )
        except Exception:
            self._search_enabled.discard(alias_id)
            return False
        self._search_enabled.add(alias_id)
        return True

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self.endpoints.values()))


def _input_modalities(input: LLMInput) -> set[InputModality]:
    modalities: set[InputModality] = {"text"}
    for item in input.messages:
        content = getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, ContentItem):
                continue
            if part.kind in {"image", "audio"}:
                modalities.add(cast(InputModality, part.kind))
            elif part.kind == "file" and part.mime.startswith("video/"):
                modalities.add("video")
    return modalities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contains_verifiable_source(value: object) -> bool:
    """Accept only structured citation/search fields containing an HTTP URL."""
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key in {"url", "uri"}
                and isinstance(child, str)
                and child.startswith(("https://", "http://"))
            ):
                return True
            if key in {
                "annotations",
                "citations",
                "sources",
                "search_results",
            } and _contains_verifiable_source(child):
                return True
            if isinstance(child, (dict, list)) and _contains_verifiable_source(child):
                return True
    elif isinstance(value, list):
        return any(_contains_verifiable_source(item) for item in value)
    return False


_FIXER_SYSTEM_PROMPT = """Answer every subquestion in the user's prompt in one self-contained response. Distinguish sourced facts, reasonable inference, and unknowns. Use hosted web search and ground material conclusions in the returned sources. Prefer primary and current sources. The API transports citations separately, so write a clear synthesis without vendor-specific JSON."""


def _normalize_citations(value: object) -> list[HostedSearchCitation]:
    found: list[HostedSearchCitation] = []
    seen: set[str] = set()

    def visit(node: object, in_sources: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, in_sources)
            return
        if not isinstance(node, dict):
            return
        data = cast(dict[str, object], node)
        if in_sources:
            raw_url = data.get("url") or data.get("uri")
            if isinstance(raw_url, str):
                parsed = urlparse(raw_url)
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    normalized = parsed._replace(fragment="").geturl()
                    if normalized not in seen:
                        title = data.get("title")
                        found.append(
                            HostedSearchCitation(
                                normalized,
                                title.strip() if isinstance(title, str) else "",
                            )
                        )
                        seen.add(normalized)
        for key, child in data.items():
            visit(
                child,
                in_sources or key in {"annotations", "citations", "sources", "search_results"},
            )

    visit(value)
    return found


def _usage_from_chunk(usage: object) -> Usage:
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    input_details = _numeric_details(getattr(usage, "prompt_tokens_details", None))
    output_details = _numeric_details(getattr(usage, "completion_tokens_details", None))
    cached = int(input_details.get("cached_tokens", 0))
    cache_write = int(
        input_details.get("cache_write_tokens", 0)
        or input_details.get("cache_creation_tokens", 0)
        or input_details.get("cache_creation_input_tokens", 0)
    )
    return Usage(
        int(prompt_tokens),
        int(cached),
        cache_write,
        int(completion_tokens),
        input_details,
        output_details,
    )


def _numeric_details(value: object) -> dict[str, int | float]:
    if value is None:
        return {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        raw = model_dump(mode="json")
    elif isinstance(value, dict):
        raw = value
    else:
        raw = vars(value) if hasattr(value, "__dict__") else {}
    return {
        str(key): number
        for key, number in raw.items()
        if isinstance(number, int | float) and not isinstance(number, bool)
    }


def _account_from_chunk(chunk: ChatCompletionChunk) -> dict[str, object]:
    """Keep only non-secret upstream attribution fields returned in-band."""
    account: dict[str, object] = {}
    if chunk.model:
        account["model"] = chunk.model
    if chunk.id:
        account["response_id"] = chunk.id
    if chunk.system_fingerprint:
        account["system_fingerprint"] = chunk.system_fingerprint
    for source, target in (("deployment", "deployment"), ("deployment_id", "deployment")):
        value = _extra_value(chunk, source)
        if isinstance(value, str) and value:
            account[target] = value
    return account


def _with_gateway_latency(
    account: dict[str, object], started: float
) -> dict[str, object]:
    return {
        **account,
        "gateway_latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _extra_value(value: object, key: str) -> object:
    extra = getattr(value, "model_extra", None)
    return extra.get(key) if isinstance(extra, dict) else None


# ---------------------------------------------------------------------------
# Message encoding (reused from openai.py)
# ---------------------------------------------------------------------------


def _messages(
    history: list[HistoryItem], workspace: Path, cache: CachePool
) -> list[ChatCompletionMessageParam]:
    messages: list[dict[str, object]] = []
    assistant: dict[str, object] | None = None

    def flush_assistant() -> None:
        nonlocal assistant
        if assistant is not None:
            messages.append(assistant)
            assistant = None

    for item in history:
        if isinstance(item, InputMessage):
            flush_assistant()
            content = _input_content(item.content, workspace, cache)
            if item.role == "developer":
                messages.append({"role": "system", "content": content})
            else:
                messages.append(
                    {"role": item.role, "name": item.name, "content": content}
                )
        elif isinstance(item, GenReasoning):
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            existing = assistant.get("reasoning_content", "")
            assistant["reasoning_content"] = f"{existing}{item.text}"
        elif isinstance(item, GenText):
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            existing = assistant.get("content", "")
            assistant["content"] = f"{existing}{item.text}"
        elif isinstance(item, GenToolCall):
            if assistant is None:
                assistant = {"role": "assistant", "content": None, "tool_calls": []}
            elif "tool_calls" not in assistant:
                assistant["tool_calls"] = []
                assistant["content"] = assistant.get("content") or None
            tool_calls = cast(list[dict[str, object]], assistant["tool_calls"])
            tool_calls.append(
                {
                    "id": item.id,
                    "type": "function",
                    "function": {"name": item.name, "arguments": item.arguments},
                }
            )
        elif isinstance(item, ToolResult):
            flush_assistant()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_call_id,
                    "content": _input_content(item.content, workspace, cache),
                }
            )
    flush_assistant()
    return cast(list[ChatCompletionMessageParam], messages)


def _input_content(
    content: list[ContentItem], workspace: Path, cache: CachePool
) -> str | list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    for item in content:
        if item.kind == "text" and item.text:
            parts.append({"type": "text", "text": item.text})
        elif item.kind == "image" and item.url:
            parts.append({"type": "image_url", "image_url": {"url": item.url}})
        elif item.kind == "image" and item.path:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(item, workspace, cache)},
                }
            )
    if not parts:
        return ""
    if len(parts) == 1 and parts[0]["type"] == "text":
        return cast(str, parts[0]["text"])
    return parts


def _image_data_url(item: ContentItem, workspace: Path, cache: CachePool) -> str:
    path = _content_path(item.path, workspace)
    stat = path.stat()
    mime = item.mime if item.mime.startswith("image/") else "image/*"
    key = f"content-image-v1:{path}:{stat.st_mtime_ns}:{stat.st_size}:{mime}"
    try:
        _, cached = cache.get(key)
        data_url = cast(_CachedImageDataUrl, cached)
    except KeyError:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = _CachedImageDataUrl(f"data:{mime};base64,{encoded}")
        cache.set(key, {"mime": mime}, data_url)
    return data_url.value


def _content_path(value: str, workspace: Path) -> Path:
    return safe_workspace_path(workspace, value, allow_absolute=True)


# ---------------------------------------------------------------------------
# Chunk parsing (reused from openai.py)
# ---------------------------------------------------------------------------


def _emit_tool_name(
    index: int,
    tool_id: str,
    name: str,
    state: ToolStreamState,
    events: list[StreamEvent],
) -> None:
    if not name or index in state.named:
        return
    group_id = f"tool-{index}"
    if tool_id:
        state.ids.setdefault(index, tool_id)
    events.append(
        StreamEvent(
            group_id,
            "tool_name",
            ToolNamePayload(id=state.ids.get(index, group_id), name=name),
        )
    )
    state.named.add(index)
    for arguments in state.pending_arguments.pop(index, []):
        events.append(
            StreamEvent(
                group_id,
                "tool_arguments_delta",
                ToolArgumentsDeltaPayload(arguments),
            )
        )


def _emit_tool_arguments(
    index: int, arguments: str, state: ToolStreamState, events: list[StreamEvent]
) -> None:
    if not arguments:
        return
    if index in state.named:
        events.append(
            StreamEvent(
                f"tool-{index}",
                "tool_arguments_delta",
                ToolArgumentsDeltaPayload(arguments),
            )
        )
    else:
        state.pending_arguments.setdefault(index, []).append(arguments)


def _events_from_chunk(
    chunk: ChatCompletionChunk, state: ToolStreamState
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    for choice in chunk.choices:
        delta = choice.delta
        if delta.content:
            events.append(
                StreamEvent("text-0", "text_delta", TextDeltaPayload(delta.content))
            )
        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            events.append(
                StreamEvent(
                    "reasoning-0",
                    "reasoning_delta",
                    ReasoningDeltaPayload(reasoning),
                )
            )
        if delta.function_call is not None:
            state.seen.add(0)
            state.ids.setdefault(0, "tool-0")
            _emit_tool_name(0, "tool-0", delta.function_call.name or "", state, events)
            _emit_tool_arguments(0, delta.function_call.arguments or "", state, events)
        for call in delta.tool_calls or []:
            if call.type is not None and call.type != "function":
                continue
            state.seen.add(call.index)
            if call.id:
                state.ids.setdefault(call.index, call.id)
            name = call.function.name if call.function and call.function.name else ""
            _emit_tool_name(
                call.index, call.id or f"tool-{call.index}", name, state, events
            )
            arguments = (
                call.function.arguments
                if call.function and call.function.arguments
                else ""
            )
            _emit_tool_arguments(call.index, arguments, state, events)
    return events
