"""Yuubot application service layer.

Owns durable business records (LLM configs, Integration records, Actor
records), maps them onto runtime objects, and exposes the chat / interrupt /
snapshot entry points consumed by the HTTP, WebSocket, and CLI facades.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import msgspec
from attrs import define, field

from ..actor import Actor, ActorConfig
from ..actor.workspace import resolve_actor_workspace_path
from ..chat import Conversation
from ..db import Database, auto_legacy_db, migrate_legacy
from ..integrations import Integration, IntegrationRecord
from ..runtime.inbound import (
    ActorInboundBody,
    InboundEnvelope,
    deliver_actor_inbound,
    deliver_app_webhook,
)
from ..domain.messages import ContentItem, GenOutput, InputMessage, ModelCard, text_content
from ..domain.stream import StreamEvent
from ..llm import Provider, ProviderInput, ProviderRecord, is_configured, model_card_from_input, provider_configured, refresh_catalog
from ..llm.types import AccountSnapshot, ModelCardInput, ProviderSnapshot, ValidationResult
from ..runtime import IncomingMessage, Runtime
from .snapshots import (
    BootstrapSnapshot,
    ConversationSummary,
    IntegrationSnapshot,
    RuntimeSnapshot,
    TaskSnapshot,
    bootstrap_snapshot as build_bootstrap_snapshot,
    conversation_summaries as build_conversation_summaries,
    integration_snapshots as build_integration_snapshots,
    runtime_snapshot as build_runtime_snapshot,
    task_snapshot_from_record,
)
from .deployment import DEFAULT_HOST, DEFAULT_PORT, ProcessConfig, load_process_config
from ..python import PythonKernelsConfig
from ..runtime.streams import TextStream
from ..runtime.cron import (
    CronAction,
    CronJobStatus,
    CronSchedule,
    PushSubscription,
    cron_job_snapshot,
    decode_cron_action,
    new_push_subscription_id,
)
from ..runtime.cron.vapid import vapid_public_key
from ..runtime.tasks import TaskDeliveryListener, register_shell_task, wait_until_terminal_or_timeout
from ..domain.records import ActorRecord, RouteRecord, lifecycle_error
from ..tools import all_tool_configs, uninstall_tools

ChatInput = str | list[ContentItem]


@define
class Yuubot:
    runtime: Runtime
    provider_records: dict[str, ProviderRecord] = field(factory=dict)
    provider_instances: dict[str, Provider] = field(factory=dict)
    integration_records: dict[str, IntegrationRecord] = field(factory=dict)
    actor_records: dict[str, ActorRecord] = field(factory=dict)
    config_path: Path | None = None
    server_host: str = DEFAULT_HOST
    server_port: int = DEFAULT_PORT
    _shutdown: bool = field(default=False, init=False)

    @property
    def actors(self) -> dict[str, Actor]:
        return self.runtime.actors

    @classmethod
    async def create(
        cls,
        data_dir: str | Path,
        *,
        python_kernels: PythonKernelsConfig | None = None,
    ) -> "Yuubot":
        root = Path(data_dir)
        db_path = root / "db" / "yuubot.db"
        legacy_db = auto_legacy_db(root) if not db_path.exists() else None
        db = await Database.open(root / "db")
        if legacy_db is not None:
            await migrate_legacy(db, data_dir=root, legacy_db=legacy_db)
        app = cls(Runtime.create(root, db, kernels=python_kernels))
        await app._load_application_state()
        return app

    @classmethod
    async def from_config(cls, config: ProcessConfig, providers: Mapping[str, Provider] | None = None) -> "Yuubot":
        app = await cls.create(config.data_dir, python_kernels=config.python_kernels)
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
            live_workspace=actor.config.workspace if actor is not None else None,
            record=self.actor_records.get(actor_id),
            default_workspace_dir=self.runtime.workspace_dir,
        )

    async def _load_application_state(self) -> None:
        for record in await self.runtime.state.list_providers():
            self.provider_records[record.id] = record
        for integration_record, integration_enabled, _last_error in await self.runtime.state.load_integrations():
            self.integration_records[integration_record.type] = integration_record
            if not integration_enabled:
                continue
            try:
                self.runtime.enable_integration(integration_record)
            except Exception as exc:
                await self.runtime.state.set_integration_enabled(
                    integration_record.type, enabled=False, last_error=lifecycle_error(exc)
                )
        for actor_record, actor_enabled in await self.runtime.state.load_actor_records():
            self.actor_records[actor_record.id] = actor_record
            if not actor_enabled:
                continue
            try:
                await self.enable_actor(actor_record.id)
            except Exception as exc:
                await self.runtime.state.set_actor_status(actor_record.id, "blocked", lifecycle_error(exc))
        self.runtime.gateway.rebind(await self.runtime.state.load_routes())
        self.runtime.shares.bind_workspace_resolver(self.actor_workspace_path)
        self.runtime.resolve_actor_workspace = self.actor_workspace_path
        await self.runtime.shares.load_grants()

    async def startup(self) -> None:
        self.runtime.listeners.add(TaskDeliveryListener(self.runtime))
        await self.runtime.listeners.start()
        self.runtime.cron.start()
        await self.runtime.cron.sync_from_store()
        await self.runtime.conversations.start_background_cleanup()
        await self.runtime.shares.start_background_cleanup()

    async def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        for actor_id in list(self.actors):
            actor = self.actors.pop(actor_id)
            await self.runtime.stop_actor_task(actor_id)
            await actor.close()
        for client in self.provider_instances.values():
            await client.close()
        await self.runtime.shutdown()

    # -- Provider configuration ----------------------------------------------

    async def put_provider(self, provider_id: str, body: ProviderInput) -> ProviderRecord:
        if body.protocol not in self.runtime.provider_registry.specs():
            raise ValueError(f"unknown provider protocol: {body.protocol}")
        existing = self.provider_records.get(provider_id)
        if existing is not None and existing.protocol != body.protocol:
            raise ValueError(f"cannot change protocol for provider {provider_id}")
        merged_config = self.runtime.provider_registry.merge_config(
            body.protocol,
            body.config,
            existing.config if existing is not None else None,
        )
        self.runtime.provider_registry.decode_config(body.protocol, merged_config)
        record = ProviderRecord(
            id=provider_id,
            name=body.name,
            protocol=body.protocol,
            config=merged_config,
            last_error=existing.last_error if existing is not None else None,
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
                id=record.id,
                name=record.name,
                protocol=record.protocol,
                config=record.config,
                last_error=result.message or "validation failed",
            )
            self.provider_records[provider_id] = updated
            await self.runtime.state.put_provider(updated)
        else:
            await self.runtime.state.set_provider_last_error(provider_id, None)
            record = self.provider_records[provider_id]
            self.provider_records[provider_id] = ProviderRecord(
                id=record.id,
                name=record.name,
                protocol=record.protocol,
                config=record.config,
                last_error=None,
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
            store=self.runtime.state,
            registry=self.runtime.provider_registry,
            retain_selectors=retain,
        )

    async def put_model_card(self, provider_id: str, body: ModelCardInput) -> ModelCard:
        if provider_id not in self.provider_records:
            raise KeyError(provider_id)
        card = model_card_from_input(body)
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
            id=record.id,
            name=record.name,
            protocol=record.protocol,
            configured=provider_configured(record),
            last_error=record.last_error,
            model_count=len(cards),
            configured_model_count=len(configured_cards),
        )

    def redacted_provider_detail(self, record: ProviderRecord, cards: list[ModelCard]) -> dict[str, object]:
        return {
            "id": record.id,
            "name": record.name,
            "protocol": record.protocol,
            "config": self.runtime.provider_registry.redact_config(record.protocol, record.config),
            "configured": provider_configured(record),
            "last_error": record.last_error,
            "model_cards": [msgspec.to_builtins(card) for card in cards],
        }

    # -- Integration lifecycle -----------------------------------------------

    async def configure_integration(self, record: IntegrationRecord) -> None:
        self.integration_records[record.type] = record
        enabled = record.name in self.runtime.integrations
        if enabled:
            # Hot-reload: replace the running instance with one built from the new record.
            await self.runtime.disable_integration(record.name)
            self.runtime.enable_integration(record)
        await self.runtime.state.put_integration(record, enabled=enabled)

    async def enable_integration(self, record: IntegrationRecord) -> Integration:
        self.integration_records[record.type] = record
        try:
            integration = self.runtime.enable_integration(record)
        except Exception as exc:
            await self.runtime.state.put_integration(record, enabled=False, last_error=lifecycle_error(exc))
            raise
        await self.runtime.state.put_integration(record, enabled=True)
        return integration

    async def enable_configured_integration(self, integration_type: str) -> Integration | None:
        record = self.integration_records.get(integration_type)
        if record is None:
            return None
        return await self.enable_integration(record)

    async def disable_integration(self, integration_type: str) -> bool:
        record = self.integration_records.get(integration_type)
        if record is None:
            return False
        await self.runtime.disable_integration(record.name)
        await self.runtime.state.set_integration_enabled(record.type, enabled=False)
        return True

    # -- Actor lifecycle -----------------------------------------------------

    def create_actor(self, config: ActorConfig, provider: Provider) -> Actor:
        actor = Actor.from_config(config, self.runtime, provider)
        self.runtime.actors[config.id] = actor
        return actor

    async def put_actor_record(self, record: ActorRecord, *, enabled: bool = True) -> None:
        self.actor_records[record.id] = record
        await self.runtime.state.put_actor(record, enabled=enabled)

    async def enable_actor(self, actor_id: str) -> Actor:
        actor = self.actors.get(actor_id)
        if actor is None:
            record = self.actor_records[actor_id]
            actor = self.create_actor(self._actor_config(record), self.build_provider(record.provider))

        async def run(_stdin: TextStream, _stdout: TextStream) -> None:
            await actor.run()

        if f"actor:{actor_id}" not in self.runtime._actor_tasks:
            self.runtime.start_actor_task(actor_id, run)
        await self.runtime.state.set_actor_status(actor_id, "running", enabled=True)
        return actor

    async def disable_actor(self, actor_id: str) -> None:
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
        await self.runtime.state.set_actor_status(actor_id, "disabled", enabled=False)

    async def update_actor(self, record: ActorRecord) -> None:
        """Upsert the record and restart the actor without uninstalling tool assets."""
        was_enabled = record.id in self.actors
        if was_enabled:
            await self.disable_actor(record.id)
        await self.put_actor_record(record)
        await self.enable_actor(record.id)

    async def remove_actor(self, actor_id: str) -> bool:
        record = self.actor_records.get(actor_id)
        if record is None:
            return False
        await self.disable_actor(actor_id)
        config = self._actor_config(record)
        try:
            await uninstall_tools(all_tool_configs(), Path(config.workspace).resolve())
        except Exception as exc:
            await self.runtime.state.set_actor_status(actor_id, "disabled", lifecycle_error(exc), enabled=False)
            raise
        self.actor_records.pop(actor_id)
        self.runtime.mailboxes.pop(actor_id)
        await self.runtime.state.delete_actor(actor_id)
        return True

    def _actor_config(self, record: ActorRecord) -> ActorConfig:
        return ActorConfig(
            id=record.id,
            name=record.name,
            description=record.description,
            workspace=record.workspace or str(self.runtime.workspace_dir / record.id),
            persona=record.persona,
            model=record.model,
        )

    # -- Conversations -------------------------------------------------------

    async def chat(self, actor_id: str, input: ChatInput, conversation_id: str | None = None) -> tuple[Conversation, list[GenOutput]]:
        actor = self.actors[actor_id]
        conversation = await self.runtime.conversations.get_or_create(actor, conversation_id)
        return conversation, await conversation.run_loop(self._input_message(actor_id, input))

    async def chat_stream(self, actor_id: str, input: ChatInput, conversation_id: str | None = None) -> AsyncIterator[StreamEvent]:
        actor = self.actors[actor_id]
        conversation = await self.runtime.conversations.get_or_create(actor, conversation_id)
        message = self._input_message(actor_id, input)
        queue: asyncio.Queue[StreamEvent | BaseException | None] = asyncio.Queue()

        async def push(event: StreamEvent) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                await conversation.run_loop(message, on_event=push)
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
        return self.runtime.conversations.interrupt(conversation_id)

    def interrupt_all(self) -> list[str]:
        return self.runtime.conversations.interrupt_all()

    def _input_message(self, actor_id: str, input: ChatInput) -> InputMessage:
        content = text_content(input) if isinstance(input, str) else input
        return InputMessage(role="user", name=actor_id, content=content)

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

    async def route_incoming(self, pattern: str, actor_id: str, *, integration_type: str = "") -> RouteRecord:
        return await self.put_route(
            RouteRecord(id=pattern, integration_type=integration_type, pattern=pattern, actor_id=actor_id)
        )

    def integration_enabled(self, integration_type: str) -> bool:
        record = self.integration_records.get(integration_type)
        return record is not None and record.name in self.runtime.integrations

    async def deliver_app_webhook(self, integration_type: str, envelope: InboundEnvelope) -> dict[str, object]:
        return await deliver_app_webhook(
            integration_type=integration_type,
            envelope=envelope,
            gateway=self.runtime.gateway,
            wakeup=self.runtime.wakeup,
            emit=self.runtime.emit,
        )

    async def deliver_actor_inbound(self, actor_id: str, body: ActorInboundBody) -> dict[str, object]:
        if actor_id not in self.actor_records:
            raise KeyError(actor_id)
        return await deliver_actor_inbound(
            actor_id=actor_id,
            body=body,
            wakeup=self.runtime.wakeup,
            actor_running=actor_id in self.actors,
        )

    async def receive_incoming(
        self,
        route: str,
        text: str,
        conversation_id: str | None = None,
        source: dict[str, object] | None = None,
    ) -> bool:
        return await self.runtime.emit_incoming(
            IncomingMessage(route=route, text=text, conversation_id=conversation_id, source=source or {})
        )

    # -- Snapshots -------------------------------------------------------------

    async def bootstrap_snapshot(self) -> BootstrapSnapshot:
        return await build_bootstrap_snapshot(self)

    async def conversation_summaries(self) -> list[ConversationSummary]:
        return await build_conversation_summaries(self)

    async def conversation_summary(self, conversation_id: str) -> ConversationSummary | None:
        for summary in await self.conversation_summaries():
            if summary.id == conversation_id:
                return summary
        return None

    async def conversation_history(self, conversation_id: str) -> list[dict[str, object]]:
        return await self.runtime.history.load_interaction_wrapped(conversation_id)

    async def integration_snapshots(self) -> list[IntegrationSnapshot]:
        return await build_integration_snapshots(self)

    def runtime_snapshot(self) -> RuntimeSnapshot:
        return build_runtime_snapshot(self)

    def task_snapshot(self, task_id: str, *, include_stdout: bool = False) -> TaskSnapshot:
        return task_snapshot_from_record(self.runtime.tasks.get(task_id), include_stdout=include_stdout)

    async def submit_shell_task(
        self,
        *,
        name: str,
        shell: str,
        intro: str,
        owner: str,
        workspace: Path,
        wait_s: float = 20,
    ) -> TaskSnapshot:
        record = register_shell_task(
            self.runtime,
            name=name,
            shell=shell,
            intro=intro,
            owner=owner,
            workspace=workspace,
        )
        if wait_s > 0:
            await wait_until_terminal_or_timeout(self.runtime.tasks, record.id, timeout=wait_s)
        return task_snapshot_from_record(record, include_stdout=True)

    async def create_cron_job(
        self,
        *,
        owner: str,
        name: str,
        schedule: CronSchedule | Mapping[str, object],
        action: CronAction | Mapping[str, object],
        once: bool = False,
    ) -> dict[str, object]:
        parsed_schedule = schedule if isinstance(schedule, CronSchedule) else msgspec.convert(schedule, CronSchedule)
        parsed_action = action if isinstance(action, CronAction) else decode_cron_action(dict(action))
        job = await self.runtime.cron_jobs.build_new(
            owner=owner,
            name=name,
            schedule=parsed_schedule,
            action=parsed_action,
            once=once,
        )
        stored = await self.runtime.cron.register(job)
        return cron_job_snapshot(stored)

    async def list_cron_jobs(
        self,
        *,
        owner: str | None = None,
        status: CronJobStatus | str | None = None,
        name_glob: str = "",
    ) -> list[dict[str, object]]:
        parsed_status = status if status in {"active", "paused", "completed", "cancelled"} else None
        jobs = await self.runtime.cron_jobs.list_jobs(
            owner=owner,
            status=parsed_status,
            name_glob=name_glob,
        )
        if status is not None and parsed_status is None:
            jobs = [job for job in jobs if job.status == status]
        return [cron_job_snapshot(job) for job in jobs]

    async def get_cron_job(self, job_id: str) -> dict[str, object]:
        return cron_job_snapshot(await self.runtime.cron_jobs.get(job_id))

    async def pause_cron_job(self, job_id: str) -> dict[str, object]:
        return cron_job_snapshot(await self.runtime.cron.pause(job_id))

    async def resume_cron_job(self, job_id: str) -> dict[str, object]:
        return cron_job_snapshot(await self.runtime.cron.resume(job_id))

    async def delete_cron_job(self, job_id: str) -> bool:
        return await self.runtime.cron.delete(job_id)

    async def save_push_subscription(self, *, endpoint: str, keys: dict[str, str]) -> dict[str, object]:
        existing = await self.runtime.push_subscriptions.find_by_endpoint(endpoint)
        subscription = PushSubscription(
            id=existing.id if existing is not None else new_push_subscription_id(),
            endpoint=endpoint,
            keys=keys,
            created_at=existing.created_at if existing is not None else "",
        )
        stored = await self.runtime.push_subscriptions.put(subscription)
        return msgspec.to_builtins(stored)

    def vapid_public_key(self) -> str:
        return vapid_public_key(self.runtime.data_dir)

