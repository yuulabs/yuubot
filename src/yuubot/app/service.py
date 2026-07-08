"""Yuubot application service layer.

Owns durable business records (LLM configs, Integration records, Actor
records), maps them onto runtime objects, and exposes the chat / interrupt /
snapshot entry points consumed by the HTTP, WebSocket, and CLI facades.
"""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path

import msgspec
from attrs import define, field

from ..actor import Actor, ActorConfig
from ..actor.prompt import SessionMode
from ..actor.workspace import resolve_actor_workspace_path, resolve_workspace_path
from ..chat import Conversation
from ..db import Database, auto_legacy_db, migrate_legacy
from ..integrations import Integration, IntegrationHealth, IntegrationRecord, integration_health
from ..runtime.inbound import (
    InboundEnvelope,
    deliver_actor_inbound,
    deliver_app_webhook,
)
from ..chat.loop import StreamCallback
from ..domain.messages import ActorMessage, ContentItem, GenOutput, InputMessage, ModelCard, text_content
from ..domain.stream import StreamEvent
from ..llm import Provider, ProviderInput, ProviderRecord, has_pricing_configured, is_configured, model_card_from_input, model_card_wire, provider_configured, refresh_catalog
from ..llm.types import AccountSnapshot, ModelCardInput, ProviderSnapshot, ValidationResult
from ..runtime import Runtime
from ..runtime.credentials import CredentialRecord
from ..runtime.kv import JsonDocument
from ..runtime.shares import ShareGrant
from ..runtime.auth_attempts import (
    AuthAttempt,
    AuthAttemptCreate,
    AuthAttemptStatus,
    auth_attempt_expires_at,
    auth_attempt_is_expired,
    new_auth_attempt,
    transition_auth_attempt,
)
from ..runtime.mcp import (
    McpCapabilityIndex,
    McpServerRecord,
    McpServerState,
    OAUTH_AUTH_MODES,
    is_oauth_auth_mode,
    normalize_mcp_record,
    replace_mcp_record,
    summarize_capabilities,
)
from ..runtime.mcp_oauth import McpOAuthCoordinator, ensure_oauth_credential_id, run_mcp_oauth_attempt
from ..runtime.skills import (
    SkillCliCommandBody,
    SkillCliCommandResult,
    SkillRecord,
    SkillSummary,
    installed_global_skill_summaries,
    run_skill_cli_command,
    stored_skill,
)
from .snapshots import (
    ActorSnapshot,
    BootstrapSnapshot,
    ConversationSummary,
    IntegrationSnapshot,
    RuntimeSnapshot,
    actor_snapshot as build_actor_snapshot,
    bootstrap_snapshot as build_bootstrap_snapshot,
    conversation_summaries as build_conversation_summaries,
    conversation_summary as build_conversation_summary,
    integration_snapshot as build_integration_snapshot,
    integration_snapshots as build_integration_snapshots,
    runtime_snapshot as build_runtime_snapshot,
)
from .deployment import DEFAULT_HOST, DEFAULT_PORT, ProcessConfig, load_process_config
from ..python import PythonKernelsConfig
from ..runtime.resource_config import ResourceConfig
from ..runtime.streams import TextStream
from ..runtime.tasks import (
    TaskDelivery,
    TaskSnapshot,
    register_shell_task,
    task_record_snapshot,
    wait_until_terminal_or_timeout,
)
from ..util.secrets import merge_redacted_config
from ..util.time import utc_now_iso
from ..domain.records import ActorConfigError, ActorInput, ActorRecord, CostRow, LifecycleError, RouteRecord, lifecycle_error
from ..tools import all_tool_configs, uninstall_tools
from ..util.asyncio_ import BackgroundSweeper

ChatInput = str | list[ContentItem]
MCP_OAUTH_ATTEMPT_TTL_S = 600.0
_log = logging.getLogger(__name__)


def _integration_health_error(health: IntegrationHealth | None) -> LifecycleError | None:
    if health is None or health.status == "ready":
        return None
    return LifecycleError(health.status, health.reason or health.status)


@define
class Yuubot:
    runtime: Runtime
    provider_records: dict[str, ProviderRecord] = field(factory=dict)
    provider_instances: dict[str, Provider] = field(factory=dict)
    integration_records: dict[str, IntegrationRecord] = field(factory=dict)
    actor_records: dict[str, ActorRecord] = field(factory=dict)
    mcp_oauth: McpOAuthCoordinator = field(factory=McpOAuthCoordinator)
    config_path: Path | None = None
    server_host: str = DEFAULT_HOST
    server_port: int = DEFAULT_PORT
    _auth_attempt_sweeper: BackgroundSweeper = field(factory=BackgroundSweeper, init=False)
    _shutdown: bool = field(default=False, init=False)

    @property
    def actors(self) -> dict[str, Actor]:
        return self.runtime.actors

    @property
    def development(self) -> bool:
        return self.runtime.development

    @classmethod
    async def create(
        cls,
        data_dir: str | Path,
        python_kernels: PythonKernelsConfig | None = None,
        resources: ResourceConfig | None = None,
    ) -> "Yuubot":
        root = Path(data_dir)
        db = await Database.open(root / "db")
        legacy_db = auto_legacy_db(root) if not (root / "db" / "yuubot.db").exists() else None
        if legacy_db is not None:
            await migrate_legacy(db, root, legacy_db)
        app = cls(Runtime.create(root, db, kernels=python_kernels, resources=resources))
        await app._load_application_state()
        return app

    @classmethod
    async def from_config(cls, config: ProcessConfig, providers: Mapping[str, Provider] | None = None) -> "Yuubot":
        app = await cls.create(
            config.data_dir,
            python_kernels=config.python_kernels,
            resources=config.resources,
        )
        app.provider_instances.update(providers or {})
        return app

    @classmethod
    async def from_config_file(cls, path: str | Path, providers: Mapping[str, Provider] | None = None) -> "Yuubot":
        config_path = Path(path)
        app = await cls.from_config(load_process_config(config_path), providers)
        app.config_path = config_path
        return app

    def actor_workspace_path(self, actor_id: str) -> Path | None:
        actor = self.actors.get(actor_id)
        return resolve_actor_workspace_path(
            actor_id,
            actor.config.workspace if actor is not None else None,
            self.actor_records.get(actor_id),
            self.runtime.workspace_dir,
        )

    async def _load_application_state(self) -> None:
        enabled_integrations = 0
        failed_integrations = 0
        enabled_actors = 0
        failed_actors = 0
        for record in await self.runtime.state.list_providers():
            self.provider_records[record.id] = record
        for integration_record, integration_enabled, _last_error in await self.runtime.state.load_integrations():
            self.integration_records[integration_record.type] = integration_record
            if not integration_enabled:
                continue
            try:
                self.runtime.enable_integration(integration_record)
                enabled_integrations += 1
            except Exception as exc:
                failed_integrations += 1
                _log.exception(
                    "integration load failed integration_type=%s name=%s",
                    integration_record.type,
                    integration_record.name,
                )
                await self.runtime.state.set_integration_enabled(
                    integration_record.type, enabled=False, last_error=lifecycle_error(exc)
                )
        mcp_records: list[McpServerRecord] = []
        mcp_indexes: list[McpCapabilityIndex] = []
        mcp_errors: dict[str, str] = {}
        for mcp_record, mcp_enabled, last_error, index in await self.runtime.state.load_mcp_servers():
            record = normalize_mcp_record(
                McpServerRecord(
                    mcp_record.id,
                    mcp_record.name,
                    mcp_record.endpoint_url,
                    mcp_record.transport,
                    mcp_record.auth_mode,
                    mcp_record.credential_id,
                    mcp_record.oauth_issuer,
                    mcp_record.oauth_authorization_endpoint,
                    mcp_record.oauth_token_endpoint,
                    mcp_record.oauth_client_id,
                    mcp_record.oauth_scope,
                    mcp_enabled,
                    mcp_record.created_at,
                    mcp_record.updated_at,
                )
            )
            mcp_records.append(record)
            if index is not None:
                mcp_indexes.append(index)
            if last_error:
                mcp_errors[record.id] = last_error
        self.runtime.mcps.bind(mcp_records, mcp_indexes)
        for server_id, last_error in mcp_errors.items():
            self.runtime.mcps.states[server_id] = McpServerState("error", last_error=last_error)
        for skill in await self.runtime.state.load_skills():
            self.runtime.skills[skill.id] = skill
        for attempt in await self.runtime.state.load_auth_attempts():
            if auth_attempt_is_expired(attempt):
                await self.runtime.state.delete_auth_attempt(attempt.id)
                continue
            self.runtime.auth_attempts[attempt.id] = attempt
        for actor_record, actor_enabled in await self.runtime.state.load_actor_records():
            self.actor_records[actor_record.id] = actor_record
            if not actor_enabled:
                continue
            try:
                await self.enable_actor(actor_record.id)
                enabled_actors += 1
            except Exception as exc:
                failed_actors += 1
                _log.exception("actor load failed actor_id=%s", actor_record.id)
                await self.runtime.state.set_actor_status(actor_record.id, "blocked", lifecycle_error(exc))
        self.runtime.gateway.rebind(await self.runtime.state.load_routes())
        self.runtime.shares.bind_workspace_resolver(self.actor_workspace_path)
        self.runtime.resolve_actor_workspace = self.actor_workspace_path
        await self.runtime.shares.load_grants()
        _log.info(
            "application state loaded providers=%s integrations=%s integrations_enabled=%s integrations_failed=%s "
            "actors=%s actors_enabled=%s actors_failed=%s mcp_servers=%s skills=%s routes=%s shares=%s",
            len(self.provider_records),
            len(self.integration_records),
            enabled_integrations,
            failed_integrations,
            len(self.actor_records),
            enabled_actors,
            failed_actors,
            len(self.runtime.mcps.records),
            len(self.runtime.skills),
            len(self.runtime.gateway.routes),
            len(self.runtime.shares.list_grants()),
        )

    async def startup(self) -> None:
        _log.info(
            "application startup starting data_dir=%s db=%s providers=%s actors=%s running_actors=%s "
            "integrations=%s routes=%s mcp_servers=%s",
            self.runtime.data_dir,
            self.runtime.db.path,
            len(self.provider_records),
            len(self.actor_records),
            len(self.actors),
            len(self.runtime.integrations),
            len(self.runtime.gateway.routes),
            len(self.runtime.mcps.records),
        )
        await self.runtime.listeners.start()
        self.runtime.cron.start()
        await self.runtime.cron.sync_from_store()
        await self.runtime.conversations.start_background_cleanup()
        await self.runtime.shares.start_background_cleanup()
        await self.sweep_expired_auth_attempts()
        await self._auth_attempt_sweeper.start(300, self.sweep_expired_auth_attempts)
        await self.runtime.resource_supervisor.start()
        _log.info("application startup complete data_dir=%s", self.runtime.data_dir)

    async def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        _log.info(
            "application shutdown starting actors=%s providers=%s integrations=%s",
            len(self.actors),
            len(self.provider_instances),
            len(self.runtime.integrations),
        )
        await self._auth_attempt_sweeper.stop()
        await self.mcp_oauth.shutdown()
        for actor_id in list(self.actors):
            actor = self.actors.pop(actor_id)
            await self.runtime.stop_actor_task(actor_id)
            await actor.close()
        for client in self.provider_instances.values():
            await client.close()
        await self.runtime.shutdown()
        _log.info("application shutdown complete data_dir=%s", self.runtime.data_dir)

    # -- Provider configuration ----------------------------------------------

    async def put_provider(self, provider_id: str, body: ProviderInput) -> ProviderRecord:
        if body.protocol not in self.runtime.provider_registry.specs():
            raise ValueError(f"unknown provider protocol: {body.protocol}")
        existing = self.provider_records.get(provider_id)
        if existing is not None and existing.protocol != body.protocol:
            raise ValueError(f"cannot change protocol for provider {provider_id}")
        merged_config = merge_redacted_config(
            body.config,
            existing.config if existing is not None else None,
            frozenset(self.runtime.provider_registry.secret_fields(body.protocol)),
        )
        self.runtime.provider_registry.decode_config(body.protocol, merged_config)
        record = ProviderRecord(
            provider_id,
            body.name,
            body.protocol,
            merged_config,
            existing.last_error if existing is not None else None,
        )
        self.provider_records[provider_id] = record
        await self._drop_provider_instance(provider_id)
        await self.runtime.state.put_provider(record)
        return record

    async def delete_provider(self, provider_id: str) -> None:
        referencing = [record.id for record in self.actor_records.values() if record.provider == provider_id]
        if referencing:
            raise ValueError(f"provider is referenced by actors: {', '.join(sorted(referencing))}")
        self.provider_records.pop(provider_id, None)
        await self._drop_provider_instance(provider_id)
        await self.runtime.state.delete_provider(provider_id)

    async def validate_provider(self, provider_id: str) -> ValidationResult:
        provider = self.build_provider(provider_id)
        try:
            result = await provider.validate()
        finally:
            if provider_id not in self.provider_instances:
                await provider.close()
        if not result.ok:
            record = self.provider_records[provider_id]
            updated = ProviderRecord(
                record.id,
                record.name,
                record.protocol,
                record.config,
                result.message or "validation failed",
            )
            self.provider_records[provider_id] = updated
            await self.runtime.state.put_provider(updated)
        else:
            await self.runtime.state.set_provider_last_error(provider_id, None)
            record = self.provider_records[provider_id]
            self.provider_records[provider_id] = ProviderRecord(
                record.id,
                record.name,
                record.protocol,
                record.config,
                None,
            )
        return result

    async def provider_balance(self, provider_id: str) -> AccountSnapshot | None:
        provider = self.build_provider(provider_id)
        try:
            return await provider.get_balance()
        finally:
            if provider_id not in self.provider_instances:
                await provider.close()

    async def refresh_provider_catalog(self, provider_id: str) -> list[ModelCard]:
        retain = frozenset(
            record.model.selector
            for record in self.actor_records.values()
            if record.provider == provider_id
        )
        return await refresh_catalog(
            provider_id,
            self.runtime.state,
            self.runtime.provider_registry,
            retain,
        )

    async def put_model_card(self, provider_id: str, selector: str, body: ModelCardInput) -> ModelCard:
        if provider_id not in self.provider_records:
            raise KeyError(provider_id)
        if body.max_context_tokens is not None and body.max_context_tokens <= 0:
            raise ValueError("max context tokens must be greater than zero")
        card = model_card_from_input(selector, body)
        await self.runtime.state.upsert_model_card(provider_id, card)
        return card

    async def delete_model_card(self, provider_id: str, selector: str) -> None:
        referencing = [
            record.id
            for record in self.actor_records.values()
            if record.provider == provider_id and record.model.selector == selector
        ]
        if referencing:
            raise ValueError(f"model card is referenced by actors: {', '.join(sorted(referencing))}")
        await self.runtime.state.delete_model_card(provider_id, selector)

    async def list_model_cards(self, provider_id: str) -> list[ModelCard]:
        return await self.runtime.state.list_model_cards(provider_id)

    def build_provider(self, provider_id: str) -> Provider:
        cached = self.provider_instances.get(provider_id)
        if cached is not None:
            return cached
        record = self.provider_records[provider_id]
        provider = self.runtime.provider_registry.build(record)
        self.provider_instances[provider_id] = provider
        return provider

    async def _drop_provider_instance(self, provider_id: str) -> None:
        provider = self.provider_instances.pop(provider_id, None)
        if provider is not None:
            await provider.close()

    def provider_snapshot(self, record: ProviderRecord, cards: list[ModelCard]) -> ProviderSnapshot:
        configured_cards = [card for card in cards if is_configured(card)]
        return ProviderSnapshot(
            record.id,
            record.name,
            record.protocol,
            provider_configured(record),
            record.last_error,
            len(cards),
            len(configured_cards),
        )

    def redacted_provider_detail(self, record: ProviderRecord, cards: list[ModelCard]) -> dict[str, object]:
        return {
            "id": record.id,
            "name": record.name,
            "protocol": record.protocol,
            "config": self.runtime.provider_registry.redact_config(record.protocol, record.config),
            "configured": provider_configured(record),
            "last_error": record.last_error,
            "model_cards": [model_card_wire(card) for card in cards],
        }

    # -- Integration lifecycle -----------------------------------------------

    async def configure_integration(self, record: IntegrationRecord) -> None:
        record = self._normalized_integration_record(record)
        _log.info(
            "integration configuring integration_type=%s name=%s",
            record.type,
            record.name,
        )
        self.integration_records[record.type] = record
        enabled = record.name in self.runtime.integrations
        last_error: LifecycleError | None = None
        if enabled:
            # Hot-reload: replace the running instance with one built from the new record.
            await self.runtime.disable_integration(record.name)
            integration = self.runtime.enable_integration(record)
            last_error = _integration_health_error(await integration_health(integration))
        await self.runtime.state.put_integration(record, enabled=enabled, last_error=last_error)
        _log.info(
            "integration configured integration_type=%s name=%s enabled=%s last_error=%s",
            record.type,
            record.name,
            enabled,
            last_error.message if last_error is not None else None,
        )

    async def enable_integration(self, record: IntegrationRecord) -> Integration:
        record = self._normalized_integration_record(record)
        _log.info(
            "integration enable requested integration_type=%s name=%s",
            record.type,
            record.name,
        )
        self.integration_records[record.type] = record
        try:
            integration = self.runtime.enable_integration(record)
        except Exception as exc:
            _log.exception(
                "integration enable failed integration_type=%s name=%s",
                record.type,
                record.name,
            )
            await self.runtime.state.put_integration(record, enabled=False, last_error=lifecycle_error(exc))
            raise
        await self.runtime.state.put_integration(
            record,
            enabled=True,
            last_error=_integration_health_error(await integration_health(integration)),
        )
        _log.info(
            "integration enable complete integration_type=%s name=%s",
            record.type,
            record.name,
        )
        return integration

    async def enable_configured_integration(self, integration_type: str) -> Integration | None:
        record = self.integration_records.get(integration_type)
        if record is None:
            config = self.runtime.integration_registry.default_config(integration_type)
            if config is None:
                return None
            record = IntegrationRecord(integration_type, integration_type, integration_type, config)
        return await self.enable_integration(record)

    async def disable_integration(self, integration_type: str) -> bool:
        record = self.integration_records.get(integration_type)
        if record is None:
            _log.info("integration disable skipped missing integration_type=%s", integration_type)
            return False
        _log.info(
            "integration disable requested integration_type=%s name=%s",
            record.type,
            record.name,
        )
        await self.runtime.disable_integration(record.name)
        await self.runtime.state.set_integration_enabled(record.type, enabled=False)
        _log.info(
            "integration disable complete integration_type=%s name=%s",
            record.type,
            record.name,
        )
        return True

    def _normalized_integration_record(self, record: IntegrationRecord) -> IntegrationRecord:
        spec = self.runtime.integration_registry.specs()[record.type]
        config = msgspec.to_builtins(msgspec.convert(record.config, spec.config_type))
        if not isinstance(config, dict):
            raise TypeError("integration config must be an object")
        return IntegrationRecord(record.id, record.type, record.name, config)

    # -- MCP data source lifecycle -------------------------------------------

    def _store_mcp_record(self, record: McpServerRecord) -> McpServerRecord:
        stored = normalize_mcp_record(record)
        self.runtime.mcps.records[stored.id] = stored
        return stored

    async def _persist_mcp_record(
        self,
        record: McpServerRecord,
        last_error: str | None = None,
        capabilities: McpCapabilityIndex | None = None,
    ) -> McpServerRecord:
        stored = self._store_mcp_record(record)
        await self.runtime.state.put_mcp_server(
            stored,
            enabled=stored.enabled,
            last_error=last_error,
            capabilities=capabilities,
        )
        return stored

    async def configure_mcp_server(
        self,
        record: McpServerRecord,
        api_key: str = "",
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer ",
        oauth_client_secret: str = "",
    ) -> McpServerRecord:
        incoming = normalize_mcp_record(record)
        existing = self.runtime.mcps.records.get(incoming.id)
        now = utc_now_iso()
        credential_id = incoming.credential_id
        if is_oauth_auth_mode(incoming.auth_mode):
            credential_id = (
                existing.credential_id
                if existing is not None and is_oauth_auth_mode(existing.auth_mode)
                else incoming.credential_id or f"mcp:{incoming.id}:oauth"
            )
        if incoming.auth_mode == "api_key" and api_key:
            credential_id = (
                existing.credential_id
                if existing is not None and existing.auth_mode == "api_key"
                else incoming.credential_id or f"mcp:{incoming.id}:api_key"
            )
            if credential_id is None:
                raise ValueError("api key credential id is required")
            await self.runtime.credentials.put(
                CredentialRecord(
                    id=credential_id,
                    kind="api_key",
                    provider=incoming.id,
                    label=f"{incoming.name} API key",
                    redacted_summary="configured",
                ),
                secret_payload={
                    "api_key": api_key,
                    "header": api_key_header,
                    "prefix": api_key_prefix,
                },
            )
        if incoming.auth_mode == "oauth_manual" and oauth_client_secret:
            credential_id = credential_id or f"mcp:{incoming.id}:oauth"
            payload = await self.runtime.credentials.secret_payload(credential_id) or {}
            payload["manual_client_secret"] = oauth_client_secret
            await self.runtime.credentials.put(
                CredentialRecord(
                    id=credential_id,
                    kind="oauth_token",
                    provider=incoming.id,
                    label=f"{incoming.name} OAuth token",
                    redacted_summary="manual client configured",
                ),
                secret_payload=payload,
            )
        stored = replace_mcp_record(
            incoming,
            credential_id=credential_id,
            created_at=existing.created_at if existing is not None and existing.created_at else now,
            updated_at=now,
        )
        return await self._persist_mcp_record(stored)

    async def enable_mcp_server(self, server_id: str) -> McpServerState:
        record = self.runtime.mcps.records[server_id]
        enabled = replace_mcp_record(record, enabled=True, updated_at=utc_now_iso())
        await self._persist_mcp_record(enabled)
        await self.runtime.state.set_mcp_server_enabled(server_id, enabled=True)
        return await self.refresh_mcp_server(server_id)

    async def disable_mcp_server(self, server_id: str) -> bool:
        record = self.runtime.mcps.records.get(server_id)
        if record is None:
            return False
        disabled = replace_mcp_record(record, enabled=False, updated_at=utc_now_iso())
        self._store_mcp_record(disabled)
        self.runtime.mcps.states[server_id] = McpServerState("disabled")
        await self.runtime.state.set_mcp_server_enabled(server_id, enabled=False)
        return True

    async def delete_mcp_server(self, server_id: str) -> bool:
        record = self.runtime.mcps.records.pop(server_id, None)
        self.runtime.mcps.states.pop(server_id, None)
        self.runtime.mcps.indexes.pop(server_id, None)
        self.mcp_oauth.cancel_for_server(server_id, self.runtime.auth_attempts)
        if record is not None and record.credential_id:
            await self.runtime.credentials.delete(record.credential_id)
        return await self.runtime.state.delete_mcp_server(server_id)

    async def refresh_mcp_server(self, server_id: str) -> McpServerState:
        record = self.runtime.mcps.records[server_id]
        if not record.enabled:
            state = McpServerState("disabled")
            self.runtime.mcps.states[server_id] = state
            return state
        if is_oauth_auth_mode(record.auth_mode) and not await self.runtime.mcps.has_oauth_tokens(record):
            state = McpServerState(
                "needs_auth",
                action_hint={
                    "kind": "start_mcp_oauth",
                    "server_id": server_id,
                    "title": f"Authorize {record.name}",
                },
                last_checked_at=utc_now_iso(),
            )
            self.runtime.mcps.states[server_id] = state
            await self.runtime.state.put_mcp_server(record, enabled=True, last_error=None)
            return state
        self.runtime.mcps.states[server_id] = McpServerState("checking", last_checked_at=utc_now_iso())
        try:
            index = await self.runtime.mcps.discover(record)
        except Exception as exc:
            if is_oauth_auth_mode(record.auth_mode):
                state = McpServerState(
                    "needs_auth",
                    last_error=str(exc),
                    action_hint={
                        "kind": "start_mcp_oauth",
                        "server_id": server_id,
                        "title": f"Reauthorize {record.name}",
                    },
                    last_checked_at=utc_now_iso(),
                )
                self.runtime.mcps.states[server_id] = state
                await self.runtime.state.put_mcp_server(record, enabled=True, last_error=str(exc))
                return state
            state = McpServerState("error", last_error=str(exc), last_checked_at=utc_now_iso())
            self.runtime.mcps.states[server_id] = state
            await self.runtime.state.put_mcp_server(record, enabled=True, last_error=str(exc))
            return state
        self.runtime.mcps.indexes[server_id] = index
        state = McpServerState(
            "ready",
            summarize_capabilities(index),
            last_checked_at=utc_now_iso(),
        )
        self.runtime.mcps.states[server_id] = state
        await self.runtime.state.put_mcp_server(record, enabled=True, capabilities=index)
        return state

    async def start_mcp_oauth(self, server_id: str, public_url_base: str) -> AuthAttempt:
        record = self.runtime.mcps.records[server_id]
        if not is_oauth_auth_mode(record.auth_mode):
            raise ValueError(f"MCP server {server_id} is not configured for OAuth")
        record = ensure_oauth_credential_id(record)
        if record.credential_id != self.runtime.mcps.records[server_id].credential_id:
            await self._persist_mcp_record(record)
        callback_token = secrets.token_urlsafe(32)
        attempt = await self.create_auth_attempt(
            AuthAttemptCreate(
                f"mcp:{server_id}",
                "oauth_pkce",
                {
                    "kind": "preparing_oauth",
                    "server_id": server_id,
                    "title": f"Authorize {record.name}",
                    "callback_token": callback_token,
                },
                auth_attempt_expires_at(MCP_OAUTH_ATTEMPT_TTL_S),
            )
        )
        redirect_uri = f"{public_url_base.rstrip('/')}/api/mcp-oauth/{attempt.id}/callback?token={callback_token}"
        future = self.mcp_oauth.begin(attempt.id)
        self.mcp_oauth.start_task(
            attempt.id,
            run_mcp_oauth_attempt(
                record,
                attempt.id,
                redirect_uri,
                future,
                self.runtime.mcps,
                self.runtime.state,
                self.runtime.auth_attempts,
                self.update_auth_attempt,
                self.mcp_oauth,
            ),
        )
        return attempt

    async def complete_mcp_oauth_callback(self, attempt_id: str, code: str, state: str | None, token: str) -> AuthAttempt:
        if not code:
            raise ValueError("OAuth callback code is required")
        attempt = self.runtime.auth_attempts.get(attempt_id)
        if attempt is None:
            raise KeyError(attempt_id)
        expected_token = attempt.action.get("callback_token")
        if not isinstance(expected_token, str) or not secrets.compare_digest(token, expected_token):
            raise ValueError("OAuth callback token is invalid")
        self.mcp_oauth.complete(attempt_id, code, state)
        return await self.update_auth_attempt(attempt_id, status="exchanging")

    async def mcp_server_snapshots(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for record in sorted(self.runtime.mcps.records.values(), key=lambda item: item.id):
            state = self.runtime.mcps.states.get(record.id)
            index = self.runtime.mcps.indexes.get(record.id)
            credential_configured = False
            if record.credential_id:
                credential_configured = await self.runtime.credentials.get(record.credential_id) is not None
            items.append({
                "id": record.id,
                "name": record.name,
                "endpoint_url": record.endpoint_url,
                "transport": record.transport,
                "auth_mode": record.auth_mode,
                "oauth_issuer": record.oauth_issuer,
                "oauth_authorization_endpoint": record.oauth_authorization_endpoint,
                "oauth_token_endpoint": record.oauth_token_endpoint,
                "oauth_client_id": record.oauth_client_id,
                "oauth_scope": record.oauth_scope,
                "credential_configured": credential_configured,
                "enabled": record.enabled,
                "status": state.status if state is not None else ("disabled" if not record.enabled else "checking"),
                "capabilities_summary": state.capabilities_summary if state is not None else "",
                "last_error": state.last_error if state is not None else None,
                "action_hint": state.action_hint if state is not None else None,
                "last_checked_at": state.last_checked_at if state is not None else None,
                "tools_count": len(index.tools) if index is not None else 0,
                "resources_count": len(index.resources) if index is not None else 0,
                "prompts_count": len(index.prompts) if index is not None else 0,
            })
        return items

    async def credential_snapshots(self) -> list[CredentialRecord]:
        return await self.runtime.credentials.list_records()

    async def delete_credential(self, credential_id: str) -> bool:
        for record_id, record in list(self.runtime.mcps.records.items()):
            if record.credential_id != credential_id:
                continue
            updated = replace_mcp_record(record, credential_id=None, updated_at=utc_now_iso())
            self._store_mcp_record(updated)
            self.runtime.mcps.states[record_id] = McpServerState(
                "needs_auth" if record.auth_mode in {"api_key", *OAUTH_AUTH_MODES} else "checking",
                action_hint={"kind": "configure_credentials", "server_id": record.id, "title": f"Configure {record.name} credentials"},
                last_checked_at=utc_now_iso(),
            )
            await self.runtime.state.put_mcp_server(updated, enabled=updated.enabled)
        return await self.runtime.credentials.delete(credential_id)

    # -- Skills ---------------------------------------------------------------

    def skill_summaries(self) -> list[SkillSummary]:
        return self.runtime.skill_summaries()

    async def installed_skill_summaries(self) -> list[SkillSummary]:
        return await installed_global_skill_summaries()

    async def run_skill_command(self, body: SkillCliCommandBody) -> SkillCliCommandResult:
        result = await run_skill_cli_command(body)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"skills exited with {result.exit_code}"
            raise RuntimeError(detail)
        return result

    async def put_skill(self, record: SkillRecord) -> SkillRecord:
        existing = self.runtime.skills.get(record.id)
        stored = stored_skill(record, existing)
        self.runtime.skills[stored.id] = stored
        await self.runtime.state.put_skill(stored)
        return stored

    async def delete_skill(self, skill_id: str) -> bool:
        self.runtime.skills.pop(skill_id, None)
        return await self.runtime.state.delete_skill(skill_id)

    # -- Auth attempts --------------------------------------------------------

    def auth_attempt_snapshots(self) -> list[AuthAttempt]:
        return sorted(self.runtime.auth_attempts.values(), key=lambda item: item.updated_at, reverse=True)

    async def wait_auth_attempt(
        self,
        attempt_id: str,
        predicate: Callable[[AuthAttempt], bool],
        timeout: float,
    ) -> AuthAttempt | None:
        return await self.runtime.auth_attempts.wait_for(attempt_id, predicate, timeout)

    async def sweep_expired_auth_attempts(self) -> None:
        for attempt_id in self.runtime.auth_attempts.expired_ids():
            await self.delete_auth_attempt(attempt_id)

    async def create_auth_attempt(self, body: AuthAttemptCreate) -> AuthAttempt:
        attempt = new_auth_attempt(body)
        await self.runtime.state.put_auth_attempt(attempt)
        await self.runtime.auth_attempts.put(attempt)
        return attempt

    async def update_auth_attempt(
        self,
        attempt_id: str,
        status: AuthAttemptStatus,
        error: str | None = None,
        action: dict[str, object] | None = None,
    ) -> AuthAttempt:
        attempt = self.runtime.auth_attempts[attempt_id]
        updated = transition_auth_attempt(attempt, status, error, action)
        await self.runtime.state.put_auth_attempt(updated)
        await self.runtime.auth_attempts.put(updated)
        return updated

    async def delete_auth_attempt(self, attempt_id: str) -> bool:
        self.mcp_oauth.cancel(attempt_id)
        await self.runtime.auth_attempts.discard(attempt_id)
        return await self.runtime.state.delete_auth_attempt(attempt_id)

    # -- Actor lifecycle -----------------------------------------------------

    def create_actor(self, config: ActorConfig, provider: Provider) -> Actor:
        _log.info(
            "actor creating actor_id=%s provider_model=%s workspace=%s",
            config.id,
            config.model.selector,
            config.workspace,
        )
        actor = Actor.from_config(config, self.runtime, provider)
        self.runtime.actors[config.id] = actor
        _log.info("actor created actor_id=%s", config.id)
        return actor

    async def put_actor_record(self, record: ActorRecord, enabled: bool = True) -> None:
        self.actor_records[record.id] = record
        await self.runtime.state.put_actor(record, enabled=enabled)
        _log.info(
            "actor record stored actor_id=%s provider=%s model=%s enabled=%s",
            record.id,
            record.provider,
            record.model.selector,
            enabled,
        )

    async def enable_actor(self, actor_id: str) -> Actor:
        _log.info("actor enable requested actor_id=%s", actor_id)
        actor = self.actors.get(actor_id)
        if actor is None:
            record = self.actor_records[actor_id]
            actor = self.create_actor(self._actor_config(record), self.build_provider(record.provider))

        async def run(_stdin: TextStream, _stdout: TextStream) -> None:
            await actor.run()

        if f"actor:{actor_id}" not in self.runtime._actor_tasks:
            self.runtime.start_actor_task(actor_id, run)
        await self.runtime.state.set_actor_status(actor_id, "running", enabled=True)
        _log.info("actor enable complete actor_id=%s", actor_id)
        return actor

    async def disable_actor(self, actor_id: str) -> None:
        _log.info("actor disable requested actor_id=%s", actor_id)
        actor = self.actors.pop(actor_id, None)
        if actor is not None:
            await actor.close()
            await self.runtime.stop_actor_task(actor_id)
            self.runtime.scheduler.cancel_for_owner_prefix(
                f"actor:{actor_id}:",
                skip_delivery=True,
            )
            await self.runtime.cron.pause_for_owner_prefix(f"actor:{actor_id}:")
        await self.runtime.conversations.close_for_actor(actor_id)
        self.runtime.mailboxes.pop(actor_id)
        await self.runtime.state.set_actor_status(actor_id, "disabled", enabled=False)
        _log.info("actor disable complete actor_id=%s existed=%s", actor_id, actor is not None)

    async def update_actor(self, record: ActorRecord) -> None:
        """Upsert the record and restart the actor without uninstalling tool assets."""
        was_enabled = record.id in self.actors
        _log.info("actor update requested actor_id=%s was_enabled=%s", record.id, was_enabled)
        if was_enabled:
            await self.disable_actor(record.id)
        await self.put_actor_record(record)
        await self.enable_actor(record.id)
        _log.info("actor update complete actor_id=%s", record.id)

    async def put_actor(self, actor_id: str, body: ActorInput) -> ActorRecord:
        if body.provider not in self.provider_records:
            raise ActorConfigError("configuration_required", f"unknown provider: {body.provider}")
        card = await self.runtime.state.load_model_card(body.provider, body.model.selector)
        if card is None:
            raise ActorConfigError(
                "model_selector_not_found",
                f"model selector not found: {body.model.selector}",
                {"provider_id": body.provider, "selector": body.model.selector},
            )
        if not has_pricing_configured(card):
            raise ActorConfigError(
                "model_pricing_required",
                f"model pricing is required before binding an actor: {body.model.selector}",
                {"provider_id": body.provider, "selector": body.model.selector},
            )
        if body.context_compression_tokens <= 0:
            raise ActorConfigError(
                "context_compression_tokens_invalid",
                "context compression token threshold must be greater than zero",
                {"context_compression_tokens": body.context_compression_tokens},
            )
        record = ActorRecord(
            id=actor_id,
            name=body.name,
            description=body.description,
            workspace=body.workspace,
            persona=body.persona,
            model=ModelCard(
                card.selector,
                body.model.reasoning_effort.strip(),
                card.max_context_tokens,
                card.vision,
                card.toolcall,
                card.json,
                card.input_price_per_million,
                card.cached_input_price_per_million,
                card.output_price_per_million,
            ),
            provider=body.provider,
            context_compression_tokens=body.context_compression_tokens,
        )
        await self.update_actor(record)
        return record

    async def remove_actor(self, actor_id: str) -> bool:
        record = self.actor_records.get(actor_id)
        if record is None:
            _log.info("actor remove skipped missing actor_id=%s", actor_id)
            return False
        _log.info("actor remove requested actor_id=%s", actor_id)
        await self.disable_actor(actor_id)
        config = self._actor_config(record)
        try:
            await uninstall_tools(all_tool_configs(), Path(config.workspace).resolve())
        except Exception as exc:
            _log.exception("actor tool uninstall failed actor_id=%s workspace=%s", actor_id, config.workspace)
            await self.runtime.state.set_actor_status(actor_id, "disabled", lifecycle_error(exc), enabled=False)
            raise
        self.actor_records.pop(actor_id)
        self.runtime.mailboxes.pop(actor_id)
        await self.runtime.state.delete_actor(actor_id)
        _log.info("actor remove complete actor_id=%s", actor_id)
        return True

    def _actor_config(self, record: ActorRecord) -> ActorConfig:
        workspace = resolve_workspace_path(
            record.workspace,
            self.runtime.workspace_dir,
            record.id,
        )
        return ActorConfig(
            id=record.id,
            name=record.name,
            description=record.description,
            workspace=str(workspace),
            persona=record.persona,
            model=record.model,
            context_compression_tokens=record.context_compression_tokens,
        )

    # -- Conversations -------------------------------------------------------

    async def run_user_message(
        self,
        actor_id: str,
        message: InputMessage,
        conversation_id: str | None = None,
        on_event: StreamCallback | None = None,
        session_mode: SessionMode = "conversation",
    ) -> list[GenOutput]:
        _log.info(
            "user message run requested actor_id=%s conversation_id=%s session_mode=%s",
            actor_id,
            conversation_id,
            session_mode,
        )
        actor = self.actors[actor_id]
        conversation = await self.runtime.conversations.get_or_create(actor, conversation_id)
        return await conversation.run_loop(message, on_event, session_mode)

    async def chat(self, actor_id: str, input: ChatInput, conversation_id: str | None = None) -> tuple[Conversation, list[GenOutput]]:
        message = self._input_message(actor_id, input)
        _log.info("chat requested actor_id=%s conversation_id=%s", actor_id, conversation_id)
        actor = self.actors[actor_id]
        conversation = await self.runtime.conversations.get_or_create(actor, conversation_id)
        return conversation, await conversation.run_loop(message, session_mode="conversation")

    async def chat_stream(self, actor_id: str, input: ChatInput, conversation_id: str | None = None) -> AsyncIterator[StreamEvent]:
        message = self._input_message(actor_id, input)
        _log.info("chat stream requested actor_id=%s conversation_id=%s", actor_id, conversation_id)
        queue: asyncio.Queue[StreamEvent | BaseException | None] = asyncio.Queue()

        async def push(event: StreamEvent) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                await self.run_user_message(actor_id, message, conversation_id, on_event=push)
            except BaseException as exc:
                await queue.put(exc)
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()

    def interrupt(self, conversation_id: str) -> bool:
        interrupted = self.runtime.conversations.interrupt(conversation_id)
        _log.info("conversation interrupt requested conversation_id=%s interrupted=%s", conversation_id, interrupted)
        return interrupted

    def interrupt_all(self) -> list[str]:
        interrupted = self.runtime.conversations.interrupt_all()
        _log.info("conversation interrupt all requested count=%s", len(interrupted))
        return interrupted

    def conversation_active(self, conversation_id: str) -> bool:
        return self.runtime.conversations.has(conversation_id)

    async def conversation_costs(self, conversation_id: str) -> list[CostRow]:
        return await self.runtime.state.load_costs(conversation_id)

    async def delete_conversation(self, conversation_id: str) -> bool:
        discarded = await self.runtime.conversations.discard(conversation_id)
        deleted_data = await self.runtime.delete_conversation_data(conversation_id)
        return discarded or deleted_data

    def _input_message(self, actor_id: str, input: ChatInput) -> InputMessage:
        content = text_content(input) if isinstance(input, str) else input
        return InputMessage("user", actor_id, content)

    # -- Inbound / gateway ----------------------------------------------------

    async def _rebind_gateway_routes(self) -> None:
        self.runtime.gateway.rebind(await self.runtime.state.load_routes())

    async def list_routes(self) -> list[RouteRecord]:
        return await self.runtime.state.load_routes()

    async def put_route(self, record: RouteRecord) -> RouteRecord:
        if record.actor_id not in self.actor_records and record.actor_id not in self.actors:
            raise KeyError(record.actor_id)
        await self.runtime.state.put_route(record)
        await self._rebind_gateway_routes()
        return record

    async def delete_route(self, route_id: str) -> bool:
        deleted = await self.runtime.state.delete_route(route_id)
        if deleted:
            await self._rebind_gateway_routes()
        return deleted

    def integration_enabled(self, integration_type: str) -> bool:
        record = self.integration_records.get(integration_type)
        return record is not None and record.name in self.runtime.integrations

    async def deliver_app_webhook(self, integration_type: str, envelope: InboundEnvelope) -> dict[str, object]:
        _log.info(
            "app webhook delivery requested integration_type=%s route=%s conversation_id=%s",
            integration_type,
            envelope.route,
            envelope.conversation_id,
        )
        return await deliver_app_webhook(
            integration_type=integration_type,
            envelope=envelope,
            gateway=self.runtime.gateway,
            wakeup=self.runtime.wakeup,
            emit=self.runtime.emit,
        )

    async def deliver_actor_inbound(self, actor_id: str, body: ActorMessage) -> dict[str, object]:
        if actor_id not in self.actor_records:
            raise KeyError(actor_id)
        _log.info(
            "actor inbound delivery requested actor_id=%s conversation_id=%s source_keys=%s",
            actor_id,
            body.conversation_id,
            sorted(body.source),
        )
        return await deliver_actor_inbound(
            actor_id=actor_id,
            body=body,
            wakeup=self.runtime.wakeup,
            actor_running=actor_id in self.actors,
        )

    # -- Shares ----------------------------------------------------------------

    async def publish_share(
        self,
        actor_id: str,
        source_path: str,
        expires_at: str | None,
    ) -> ShareGrant:
        return await self.runtime.shares.publish(
            actor_id,
            source_path,
            expires_at,
        )

    def list_share_grants(self) -> list[ShareGrant]:
        return self.runtime.shares.list_grants()

    def get_share_grant(self, share_id: str) -> ShareGrant:
        return self.runtime.shares.get(share_id)

    async def revoke_share(self, share_id: str) -> ShareGrant:
        return await self.runtime.shares.revoke(share_id)

    # -- Actor KV --------------------------------------------------------------

    async def kv_get(self, actor_id: str, key: str) -> JsonDocument | None:
        return await self.runtime.kv.get(actor_id, key)

    async def kv_put(
        self,
        actor_id: str,
        key: str,
        value: object,
        if_match: str | None = None,
    ) -> JsonDocument:
        return await self.runtime.kv.put(actor_id, key, value, if_match=if_match)

    async def kv_delete(self, actor_id: str, key: str) -> bool:
        return await self.runtime.kv.delete(actor_id, key)

    # -- Push notifications ----------------------------------------------------

    def vapid_public_key(self) -> str:
        from .cron import vapid_public_key_for

        return vapid_public_key_for(self.runtime)

    async def save_push_subscription(self, endpoint: str, keys: dict[str, str]) -> dict[str, object]:
        from .cron import save_push_subscription

        return await save_push_subscription(self.runtime, endpoint, keys)

    async def delete_push_subscription(self, subscription_id: str) -> bool:
        return await self.runtime.push_subscriptions.delete(subscription_id)

    # -- Snapshots -------------------------------------------------------------

    async def bootstrap_snapshot(self) -> BootstrapSnapshot:
        return await build_bootstrap_snapshot(self)

    async def actor_snapshot(self, actor_id: str) -> ActorSnapshot | None:
        return await build_actor_snapshot(self, actor_id)

    async def conversation_summaries(self) -> list[ConversationSummary]:
        return await build_conversation_summaries(self)

    async def conversation_summary(self, conversation_id: str) -> ConversationSummary | None:
        return await build_conversation_summary(self, conversation_id)

    async def conversation_history(
        self,
        conversation_id: str,
        after_seq: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict[str, object]], bool]:
        return await self.runtime.history.load_interaction_wrapped(
            conversation_id,
            after_seq,
            limit,
        )

    async def integration_snapshot(self, integration_type: str) -> IntegrationSnapshot | None:
        return await build_integration_snapshot(self, integration_type)

    async def integration_snapshots(self) -> list[IntegrationSnapshot]:
        return await build_integration_snapshots(self)

    def runtime_snapshot(self) -> RuntimeSnapshot:
        return build_runtime_snapshot(self)

    def task_snapshot(self, task_id: str, include_stdout: bool = False) -> TaskSnapshot:
        return task_record_snapshot(self.runtime.tasks.get(task_id), include_stdout)

    def task_stdin_write(self, task_id: str, text: str) -> TaskSnapshot:
        self.runtime.write_runtime_task_stdin(task_id, text)
        return task_record_snapshot(self.runtime.tasks.get(task_id), True)

    async def submit_shell_task(
        self,
        name: str,
        shell: str,
        intro: str,
        owner: str,
        workspace: Path,
        wait_s: float = 20,
        delivery: TaskDelivery = "manual",
        ttl_s: float | None = None,
    ) -> TaskSnapshot:
        record = register_shell_task(
            self.runtime,
            name,
            shell,
            intro,
            owner,
            workspace,
            delivery,
            ttl_s,
        )
        if wait_s > 0:
            await wait_until_terminal_or_timeout(self.runtime.tasks, record.id, wait_s)
        return task_record_snapshot(record, True)
