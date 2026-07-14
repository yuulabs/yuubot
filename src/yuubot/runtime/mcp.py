"""MCP server records, discovery cache, and SDK-backed MCP client."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import httpx
import msgspec
from attrs import define, field
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken
from mcp.shared._httpx_utils import create_mcp_http_client

from .credentials import CredentialRecord, CredentialStore

McpTransport = Literal["http", "stdio"]
McpAuthMode = Literal["none", "api_key", "oauth_auto", "oauth_manual"]
McpStateStatus = Literal["disabled", "checking", "needs_auth", "ready", "degraded", "error"]
McpCapabilityKind = Literal["tool", "resource", "prompt"]

_LEGACY_AUTH_MODES: dict[str, McpAuthMode] = {
    "auto": "oauth_auto",
    "oauth": "oauth_auto",
}
OAUTH_AUTH_MODES = frozenset({"oauth_auto", "oauth_manual"})


class ApiKeySecret(msgspec.Struct, frozen=True):
    api_key: str
    header: str = "Authorization"
    prefix: str = "Bearer "


class OAuthCredentialSecret(msgspec.Struct, frozen=True):
    tokens: dict[str, object] = msgspec.field(default_factory=dict)
    client_info: dict[str, object] | None = None
    manual_client_secret: str | None = None


def normalize_auth_mode(mode: str) -> McpAuthMode:
    mapped = _LEGACY_AUTH_MODES.get(mode, mode)
    if mapped in {"none", "api_key", "oauth_auto", "oauth_manual"}:
        return cast(McpAuthMode, mapped)
    raise ValueError(f"unsupported MCP auth mode: {mode}")


def is_oauth_auth_mode(mode: str) -> bool:
    return normalize_auth_mode(mode) in OAUTH_AUTH_MODES


def normalize_mcp_record(record: McpServerRecord) -> McpServerRecord:
    return replace_mcp_record(record, auth_mode=record.auth_mode)


def replace_mcp_record(record: McpServerRecord, **changes: object) -> McpServerRecord:
    if "auth_mode" in changes and isinstance(changes["auth_mode"], str):
        changes = {**changes, "auth_mode": normalize_auth_mode(changes["auth_mode"])}
    from msgspec.structs import replace

    return replace(record, **changes)

class McpServerRecord(msgspec.Struct, frozen=True):
    id: str
    name: str
    endpoint_url: str
    transport: McpTransport = "http"
    auth_mode: McpAuthMode = "none"
    credential_id: str | None = None
    oauth_issuer: str = ""
    oauth_authorization_endpoint: str = ""
    oauth_token_endpoint: str = ""
    oauth_client_id: str = ""
    oauth_scope: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class McpCapabilitySummary(msgspec.Struct, frozen=True):
    server_id: str
    kind: McpCapabilityKind
    name: str
    description: str = ""
    uri: str = ""


class McpToolSpec(msgspec.Struct, frozen=True):
    name: str
    description: str = ""
    input_schema: dict[str, object] = msgspec.field(default_factory=dict)


class McpCapabilityIndex(msgspec.Struct, frozen=True):
    server_id: str
    tools: tuple[McpToolSpec, ...] = ()
    resources: tuple[McpCapabilitySummary, ...] = ()
    prompts: tuple[McpCapabilitySummary, ...] = ()


class McpServerState(msgspec.Struct, frozen=True):
    status: McpStateStatus
    capabilities_summary: str = ""
    last_error: str | None = None
    action_hint: dict[str, object] | None = None
    last_checked_at: str | None = None


class McpResult(msgspec.Struct, frozen=True):
    server_id: str
    name: str = ""
    content: object = None
    raw: dict[str, object] = msgspec.field(default_factory=dict)


def capability_summaries(index: McpCapabilityIndex) -> list[McpCapabilitySummary]:
    return [
        *[
            McpCapabilitySummary(
                index.server_id,
                "tool",
                tool.name,
                tool.description,
            )
            for tool in index.tools
        ],
        *index.resources,
        *index.prompts,
    ]


def summarize_capabilities(index: McpCapabilityIndex) -> str:
    return f"{len(index.tools)} tools, {len(index.resources)} resources, {len(index.prompts)} prompts"


def search_capabilities(
    indexes: Iterable[McpCapabilityIndex],
    query: str = "",
    kind: str = "",
    server: str = "",
) -> list[McpCapabilitySummary]:
    terms = [part.casefold() for part in query.split() if part]
    matches: list[McpCapabilitySummary] = []
    for summary in itertools.chain.from_iterable(capability_summaries(index) for index in indexes):
        if server and summary.server_id != server:
            continue
        if kind and summary.kind != kind:
            continue
        haystack = f"{summary.server_id} {summary.kind} {summary.name} {summary.description} {summary.uri}".casefold()
        if all(term in haystack for term in terms):
            matches.append(summary)
    return matches


def tool_signature(tool: McpToolSpec) -> str:
    params = _schema_params(tool.input_schema)
    signature = f"{tool.name}({', '.join(params)}) -> McpResult"
    description = _truncate(tool.description.strip(), 240)
    return signature if not description else f"{signature}\n{description}"


def _schema_params(schema: dict[str, object]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required_raw = schema.get("required", [])
    required = frozenset(str(item) for item in required_raw) if isinstance(required_raw, list) else frozenset()
    params: list[str] = []
    for name, value in properties.items():
        if not isinstance(name, str):
            continue
        prop = cast(dict[str, object], value) if isinstance(value, dict) else {}
        hint = _schema_type(prop)
        if name in required:
            params.append(f"{name}: {hint}")
            continue
        default = prop.get("default")
        if default is None:
            params.append(f"{name}: {hint} | None = None")
        else:
            params.append(f"{name}: {hint} = {default!r}")
    return params


def _schema_type(schema: dict[str, object]) -> str:
    enum = schema.get("enum")
    if isinstance(enum, list) and 0 < len(enum) <= 8 and all(isinstance(item, str) for item in enum):
        return "Literal[" + ", ".join(repr(item) for item in enum) + "]"
    value = schema.get("type")
    if isinstance(value, list):
        value = next((item for item in value if item != "null"), None)
    return {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
    }.get(value if isinstance(value, str) else "", "Any")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n[truncated: characters 0-{limit} of {len(value)}]"


@define
class McpManager:
    credentials: CredentialStore
    records: dict[str, McpServerRecord] = field(factory=dict)
    states: dict[str, McpServerState] = field(factory=dict)
    indexes: dict[str, McpCapabilityIndex] = field(factory=dict)

    def bind(self, records: Iterable[McpServerRecord], indexes: Iterable[McpCapabilityIndex]) -> None:
        self.records = {record.id: record for record in records}
        self.indexes = {index.server_id: index for index in indexes}
        self.states = {
            record.id: McpServerState(
                "ready" if record.enabled and record.id in self.indexes else "disabled" if not record.enabled else "checking",
                summarize_capabilities(self.indexes[record.id]) if record.id in self.indexes else "",
            )
            for record in self.records.values()
        }

    def enabled_records(self) -> list[McpServerRecord]:
        return [record for record in self.records.values() if record.enabled]

    def search(self, query: str = "", kind: str = "", server: str = "") -> list[McpCapabilitySummary]:
        enabled = {record.id for record in self.enabled_records()}
        return search_capabilities(
            (index for server_id, index in self.indexes.items() if server_id in enabled),
            query,
            kind,
            server,
        )

    def get_spec(self, server_id: str, name: str) -> str:
        index = self.indexes.get(server_id)
        if index is None:
            raise KeyError(server_id)
        for tool in index.tools:
            if tool.name == name:
                return tool_signature(tool)
        raise KeyError(name)

    async def discover(self, record: McpServerRecord) -> McpCapabilityIndex:
        client = HttpMcpClient(record, await self._auth_headers(record), await self._oauth_auth(record))
        return await client.discover()

    async def discover_with_oauth(
        self,
        record: McpServerRecord,
        redirect_uri: str,
        redirect_handler: Any,
        callback_handler: Any,
        timeout_s: float = 600.0,
    ) -> McpCapabilityIndex:
        auth = self._build_oauth_auth(
            record,
            redirect_uri,
            redirect_handler,
            callback_handler,
            timeout_s,
        )
        client = HttpMcpClient(record, {}, auth)
        return await client.discover()

    async def invoke(self, server_id: str, name: str, arguments: dict[str, object]) -> McpResult:
        record = self._enabled_record(server_id)
        client = HttpMcpClient(record, await self._auth_headers(record), await self._oauth_auth(record))
        payload = await client.call_tool(name, arguments)
        return McpResult(server_id, name, payload.get("content"), payload)

    async def read_resource(self, server_id: str, uri: str) -> McpResult:
        record = self._enabled_record(server_id)
        client = HttpMcpClient(record, await self._auth_headers(record), await self._oauth_auth(record))
        payload = await client.read_resource(uri)
        return McpResult(server_id, uri, payload.get("contents"), payload)

    def _enabled_record(self, server_id: str) -> McpServerRecord:
        record = self.records[server_id]
        if not record.enabled:
            raise RuntimeError(f"MCP server is disabled: {server_id}")
        return record

    async def _auth_headers(self, record: McpServerRecord) -> dict[str, str]:
        if record.auth_mode != "api_key" or not record.credential_id:
            return {}
        payload = await self.credentials.secret_payload(record.credential_id)
        if not payload:
            return {}
        secret = msgspec.convert(payload, ApiKeySecret)
        if not secret.api_key:
            return {}
        return {secret.header: f"{secret.prefix}{secret.api_key}"}

    async def has_oauth_tokens(self, record: McpServerRecord) -> bool:
        if not is_oauth_auth_mode(record.auth_mode) or not record.credential_id:
            return False
        payload = await self.credentials.secret_payload(record.credential_id)
        if not payload:
            return False
        secret = msgspec.convert(payload, OAuthCredentialSecret)
        return bool(secret.tokens)

    async def _oauth_auth(self, record: McpServerRecord) -> httpx.Auth | None:
        if not is_oauth_auth_mode(record.auth_mode):
            return None
        if not await self.has_oauth_tokens(record):
            return None
        return self._build_oauth_auth(record, redirect_uri=None, redirect_handler=None, callback_handler=None)

    def _build_oauth_auth(
        self,
        record: McpServerRecord,
        redirect_uri: str | None,
        redirect_handler: Any,
        callback_handler: Any,
        timeout_s: float = 300.0,
    ) -> httpx.Auth:
        if not record.credential_id:
            raise ValueError(f"MCP OAuth credential id is not configured for {record.id}")
        metadata = OAuthClientMetadata.model_validate(
            {
                "redirect_uris": [redirect_uri] if redirect_uri else None,
                "client_name": "yuubot",
                "token_endpoint_auth_method": "none",
                "scope": record.oauth_scope or None,
            }
        )
        provider = OAuthClientProvider(
            server_url=record.endpoint_url,
            client_metadata=metadata,
            storage=McpOAuthTokenStorage(self.credentials, record, redirect_uri),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=timeout_s,
        )
        if record.auth_mode == "oauth_manual" and record.oauth_authorization_endpoint and record.oauth_token_endpoint:
            issuer = record.oauth_issuer or record.endpoint_url
            provider.context.auth_server_url = issuer
            provider.context.oauth_metadata = OAuthMetadata.model_validate({
                "issuer": issuer,
                "authorization_endpoint": record.oauth_authorization_endpoint,
                "token_endpoint": record.oauth_token_endpoint,
                "scopes_supported": record.oauth_scope.split() if record.oauth_scope else None,
            })
        return provider


class HttpMcpClient:
    def __init__(self, record: McpServerRecord, auth_headers: dict[str, str], auth: httpx.Auth | None = None) -> None:
        if record.transport != "http":
            raise NotImplementedError("only remote HTTP MCP transport is supported")
        self._record = record
        self._auth_headers = auth_headers
        self._auth = auth

    async def discover(self) -> McpCapabilityIndex:
        async with self._session() as session:
            tools_result = await session.list_tools()
            tools = [
                McpToolSpec(
                    tool.name,
                    tool.description or "",
                    cast(dict[str, object], tool.inputSchema),
                )
                for tool in tools_result.tools
            ]
            resource_items = []
            prompt_items = []
            try:
                resource_items = list((await session.list_resources()).resources)
            except Exception:
                resource_items = []
            try:
                prompt_items = list((await session.list_prompts()).prompts)
            except Exception:
                prompt_items = []
            resources = [
                McpCapabilitySummary(
                    self._record.id,
                    "resource",
                    resource.name or str(resource.uri),
                    resource.description or "",
                    str(resource.uri),
                )
                for resource in resource_items
            ]
            prompts = [
                McpCapabilitySummary(
                    self._record.id,
                    "prompt",
                    prompt.name,
                    prompt.description or "",
                )
                for prompt in prompt_items
            ]
        return McpCapabilityIndex(self._record.id, tuple(tools), tuple(resources), tuple(prompts))

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        async with self._session() as session:
            result = await session.call_tool(name, arguments)
        return _model_payload(result)

    async def read_resource(self, uri: str) -> dict[str, object]:
        async with self._session() as session:
            result = await session.read_resource(cast(Any, uri))
        return _model_payload(result)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        timeout = httpx.Timeout(30.0, read=300.0)
        http_client = create_mcp_http_client(headers=self._auth_headers, timeout=timeout, auth=self._auth)
        async with http_client:
            async with streamable_http_client(self._record.endpoint_url, http_client=http_client) as (read, write, _session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


class McpOAuthTokenStorage(TokenStorage):
    def __init__(self, credentials: CredentialStore, record: McpServerRecord, redirect_uri: str | None = None) -> None:
        if not record.credential_id:
            raise ValueError(f"MCP OAuth credential id is not configured for {record.id}")
        self._credentials = credentials
        self._record = record
        self._credential_id = record.credential_id
        self._redirect_uri = redirect_uri

    async def get_tokens(self) -> OAuthToken | None:
        secret = msgspec.convert(await self._secret_payload(), OAuthCredentialSecret)
        if not secret.tokens:
            return None
        return OAuthToken.model_validate(secret.tokens)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        secret = msgspec.convert(await self._secret_payload(), OAuthCredentialSecret)
        token_payload = tokens.model_dump(mode="json", exclude_none=True)
        await self._put(
            msgspec.structs.replace(secret, tokens=token_payload),
            token_payload,
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        secret = msgspec.convert(await self._secret_payload(), OAuthCredentialSecret)
        client_info = secret.client_info
        if client_info is None:
            if not self._record.oauth_client_id:
                return None
            client_info = {
                "redirect_uris": [self._redirect_uri or "http://127.0.0.1/unused-oauth-callback"],
                "client_id": self._record.oauth_client_id,
                "client_secret": secret.manual_client_secret,
            }
            await self._put(msgspec.structs.replace(secret, client_info=client_info))
        return OAuthClientInformationFull.model_validate(client_info)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        secret = msgspec.convert(await self._secret_payload(), OAuthCredentialSecret)
        await self._put(
            msgspec.structs.replace(
                secret,
                client_info=client_info.model_dump(mode="json", exclude_none=True),
            )
        )

    async def _secret_payload(self) -> dict[str, object]:
        return await self._credentials.secret_payload(self._credential_id) or {}

    async def _put(self, secret: OAuthCredentialSecret, token_payload: dict[str, object] | None = None) -> None:
        existing = await self._credentials.get(self._credential_id)
        scopes: tuple[str, ...] = ()
        expires_at = existing.expires_at if existing is not None else None
        if token_payload is not None:
            scope = token_payload.get("scope")
            scopes = tuple(str(item) for item in str(scope).split() if item) if isinstance(scope, str) else ()
            expires_at = _token_expires_at(token_payload)
        await self._credentials.put(
            CredentialRecord(
                id=self._credential_id,
                kind="oauth_token",
                provider=self._record.id,
                label=f"{self._record.name} OAuth token",
                redacted_summary="configured",
                expires_at=expires_at,
                scopes=scopes,
                created_at=existing.created_at if existing is not None else "",
            ),
            secret_payload=msgspec.to_builtins(secret),
        )


def _token_expires_at(token_payload: dict[str, object]) -> str | None:
    expires_in = token_payload.get("expires_in")
    if not isinstance(expires_in, int | float):
        return None
    return (datetime.now(tz=UTC) + timedelta(seconds=float(expires_in))).isoformat()


def _model_payload(value: object) -> dict[str, object]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(by_alias=True, mode="json", exclude_none=True)
        return cast(dict[str, object], payload) if isinstance(payload, dict) else {"value": payload}
    return {"value": value}
