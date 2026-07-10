"""Process-wide resource owner: tasks, database, cache, eventbus, gateway, mailboxes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from ..python import KernelLimiter, PythonKernelsConfig
from ..db import Database
from .events import EventBus, ListenerHub
from .event_payloads import RuntimeEventPayload
from ..chat.history import HistoryStore
from ..integrations.registry import Integration, IntegrationRegistry, default_registry
from ..integrations.records import IntegrationRecord
from ..llm.gateway import StreamClient
from ..domain.messages import ActorMessage
from ..domain.records import RouteRecord
from .cache import CachePool
from .store import ApplicationStateStore
from .streams import TaskCoroFactory, TextStream
from .kv import KvStore
from .shares import ShareRegistry
from .credentials import CredentialStore
from .auth_attempts import AuthAttemptRegistry
from .mcp import McpManager
from .skills import SkillRecord, SkillSummary, resolve_catalog, skill_summary
from .tasks import (
    RuntimeTaskRecord,
    TaskRegistry,
    TaskScheduler,
    drain_pending_task_deliveries,
    schedule_task_delivery,
    suppress_conversation_task_deliveries,
    wait_until_terminal_or_timeout,
)
from .wakeup import WakeupDelivery
from .turn_limits import TurnLimitRegistry
from .resource_config import ResourceConfig
from .resources import ResourceSupervisor, resolve_tmp_dir
from .cron import (
    CronExecutor,
    CronJobScheduler,
    CronJobStore,
    NotificationDispatcher,
    PushSubscriptionStore,
)

if TYPE_CHECKING:
    from ..actor import Actor
    from ..chat.loop import ConversationManager

_log = logging.getLogger(__name__)


@define
class Mailbox:
    _queue: asyncio.Queue[ActorMessage] = field(factory=asyncio.Queue)

    async def send(self, message: ActorMessage) -> None:
        await self._queue.put(message)

    async def receive(self) -> ActorMessage:
        return await self._queue.get()


@define
class ActorMailboxRegistry:
    _mailboxes: dict[str, Mailbox] = field(factory=dict)

    def get(self, actor_id: str) -> Mailbox:
        return self.ensure(f"actor:{actor_id}")

    def ensure(self, address: str) -> Mailbox:
        return self._mailboxes.setdefault(address, Mailbox())

    def pop(self, actor_id: str) -> Mailbox | None:
        return self._mailboxes.pop(f"actor:{actor_id}", None)

    def __contains__(self, address: str) -> bool:
        return address in self._mailboxes

    def items(self) -> list[tuple[str, Mailbox]]:
        return list(self._mailboxes.items())

@define
class Gateway:
    """Maps inbound routes to actor ids."""

    routes: dict[str, str] = field(factory=dict)

    def rebind(self, records: list[RouteRecord]) -> None:
        self.routes = {
            record.pattern: record.actor_id
            for record in records
            if record.enabled
        }

    def resolve(self, route: str) -> str | None:
        actor_id = self.routes.get(route)
        if actor_id is not None:
            return actor_id
        for pattern, candidate in self.routes.items():
            if fnmatch(route, pattern):
                return candidate
        return None


@define
class Runtime:
    """Owner of all system resources; converts durable records into live objects."""

    data_dir: Path
    db: Database
    state: ApplicationStateStore
    history: HistoryStore
    cache: CachePool
    eventbus: EventBus
    listeners: ListenerHub
    wakeup: WakeupDelivery
    gateway: Gateway
    conversations: ConversationManager
    integration_registry: IntegrationRegistry
    gateway_client: StreamClient
    mailboxes: ActorMailboxRegistry
    tasks: TaskRegistry
    scheduler: TaskScheduler
    cron_jobs: CronJobStore
    push_subscriptions: PushSubscriptionStore
    shares: ShareRegistry
    kv: KvStore
    credentials: CredentialStore
    mcps: McpManager
    python_kernels: PythonKernelsConfig
    kernel_limiter: KernelLimiter
    turn_limits: TurnLimitRegistry
    resources_config: ResourceConfig
    resource_supervisor: ResourceSupervisor
    skills: dict[str, SkillRecord] = field(factory=dict)
    package_skills: list[SkillRecord] = field(factory=list)
    skill_discovery_warning: str = ""
    auth_attempts: AuthAttemptRegistry = field(factory=AuthAttemptRegistry)
    integrations: dict[str, Integration] = field(factory=dict)
    actors: dict[str, Actor] = field(factory=dict)
    _actor_tasks: dict[str, asyncio.Task[None]] = field(factory=dict)
    _detached_tasks: set[asyncio.Task[None]] = field(factory=set, init=False)
    _task_delivery_tasks: set[asyncio.Task[None]] = field(factory=set, init=False)
    _cron: CronJobScheduler | None = field(default=None, init=False)
    _cron_executor: CronExecutor | None = field(default=None, init=False)
    _notifications: NotificationDispatcher | None = field(default=None, init=False)
    resolve_actor_workspace: Callable[[str], Path | None] | None = None
    development: bool = False

    @property
    def cron(self) -> CronJobScheduler:
        if self._cron is None:
            raise RuntimeError("cron scheduler not initialized")
        return self._cron

    @property
    def cron_executor(self) -> CronExecutor:
        if self._cron_executor is None:
            raise RuntimeError("cron executor not initialized")
        return self._cron_executor

    @property
    def notifications(self) -> NotificationDispatcher:
        if self._notifications is None:
            raise RuntimeError("notification dispatcher not initialized")
        return self._notifications

    @classmethod
    def create(
        cls,
        data_dir: str | Path,
        db: Database,
        kernels: PythonKernelsConfig | None = None,
        resources: ResourceConfig | None = None,
        gateway_client: StreamClient | None = None,
    ) -> Runtime:
        from ..chat.loop import ConversationManager

        if gateway_client is None:
            from ..llm.gateway import GatewayClient

            gateway_client = GatewayClient()
        root = Path(data_dir)
        for sub in ("workspace", "logs", "db", "published", "kv", "tmp"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        mailboxes = ActorMailboxRegistry()
        eventbus = EventBus()
        state = ApplicationStateStore(db)
        task_registry = TaskRegistry()
        scheduler = TaskScheduler(eventbus.emit, task_registry)
        cron_jobs = CronJobStore(db)
        push_subscriptions = PushSubscriptionStore(db)
        shares = ShareRegistry(root, state, eventbus.emit)
        kv = KvStore(root)
        credentials = CredentialStore(db, root)
        mcps = McpManager(credentials)
        python_kernels = kernels or PythonKernelsConfig()
        resources_config = resources or ResourceConfig()
        resource_supervisor = ResourceSupervisor(
            root,
            root / "logs",
            db,
            resources_config,
            eventbus.emit,
        )
        runtime = cls(
            data_dir=root,
            db=db,
            state=state,
            history=HistoryStore(db),
            cache=CachePool(),
            eventbus=eventbus,
            listeners=ListenerHub(eventbus),
            wakeup=WakeupDelivery(mailboxes, eventbus.emit),
            gateway=Gateway(),
            conversations=ConversationManager(),
            integration_registry=default_registry(),
            gateway_client=gateway_client,
            mailboxes=mailboxes,
            tasks=task_registry,
            scheduler=scheduler,
            cron_jobs=cron_jobs,
            push_subscriptions=push_subscriptions,
            shares=shares,
            kv=kv,
            credentials=credentials,
            mcps=mcps,
            python_kernels=python_kernels,
            kernel_limiter=KernelLimiter(python_kernels),
            turn_limits=TurnLimitRegistry(),
            resources_config=resources_config,
            resource_supervisor=resource_supervisor,
        )
        runtime._notifications = NotificationDispatcher.create(runtime)
        runtime.scheduler.on_terminal = runtime._schedule_task_delivery

        def scheduler_ref() -> CronJobScheduler:
            if runtime._cron is None:
                raise RuntimeError("cron scheduler not initialized")
            return runtime._cron

        runtime._cron_executor = CronExecutor(runtime, scheduler_ref, runtime._resolve_workspace)
        runtime._cron = CronJobScheduler(runtime, cron_jobs, runtime._cron_executor)
        return runtime

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def tmp_dir(self) -> Path:
        return resolve_tmp_dir(self.data_dir, self.resources_config)

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    def get_mailbox(self, address: str) -> Mailbox:
        if address.startswith("actor:"):
            return self.mailboxes.get(address.removeprefix("actor:"))
        return self.mailboxes.ensure(address)

    def emit(self, payload: RuntimeEventPayload) -> None:
        from .event_payloads import ConversationStreamPayload

        if isinstance(payload, ConversationStreamPayload):
            self.conversations.schedule_delta(payload.conversation_id, payload.event)
            return
        self.eventbus.emit(payload)

    def skill_summaries(self) -> list[SkillSummary]:
        records, _items = resolve_catalog(list(self.skills.values()), self.package_skills)
        return [skill_summary(records[key]) for key in sorted(records)]

    def skill_catalog(self) -> list[SkillSummary]:
        _records, items = resolve_catalog(list(self.skills.values()), self.package_skills)
        return items

    def skill_record(self, skill_id: str) -> SkillRecord:
        records, _items = resolve_catalog(list(self.skills.values()), self.package_skills)
        return records[skill_id]

    def enable_integration(self, record: IntegrationRecord) -> Integration:
        _log.info(
            "integration enabling integration_type=%s name=%s",
            record.type,
            record.name,
        )
        integration = self.integration_registry.create(record, self)
        self.integrations[integration.name] = integration
        _log.info(
            "integration enabled integration_type=%s name=%s",
            record.type,
            record.name,
        )
        return integration

    async def disable_integration(self, name: str) -> Integration | None:
        integration = self.integrations.pop(name, None)
        if integration is not None:
            _log.info("integration disabling name=%s", name)
            await integration.close()
            self.cache.invalidate(prefix=f"integration:{name}:")
            _log.info("integration disabled name=%s", name)
        return integration

    async def delete_conversation_data(self, conversation_id: str) -> bool:
        async with self.db.transaction():
            history = await self.db.execute("delete from history where conversation_id = ?", (conversation_id,))
            conversation = await self.db.execute("delete from app_conversations where id = ?", (conversation_id,))
            usage = await self.db.execute("delete from app_usage where conversation_id = ?", (conversation_id,))
        return history.rowcount > 0 or conversation.rowcount > 0 or usage.rowcount > 0

    def start_actor_task(self, actor_id: str, coro_factory: TaskCoroFactory) -> None:
        key = f"actor:{actor_id}"
        if key in self._actor_tasks:
            _log.info("actor task already running actor_id=%s", actor_id)
            return
        _log.info("actor task starting actor_id=%s", actor_id)
        self._actor_tasks[key] = asyncio.create_task(self._run_actor_task(key, coro_factory))

    def track_detached_task(self, task: asyncio.Task[None]) -> None:
        self._detached_tasks.add(task)
        task.add_done_callback(self._detached_tasks.discard)

    async def stop_actor_task(self, actor_id: str) -> None:
        key = f"actor:{actor_id}"
        task = self._actor_tasks.pop(key, None)
        if task is None:
            _log.info("actor task stop skipped not_running actor_id=%s", actor_id)
            return
        if not task.done():
            _log.info("actor task cancelling actor_id=%s", actor_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        _log.info("actor task stopped actor_id=%s", actor_id)

    def cancel_runtime_task(self, task_id: str) -> None:
        record = self.tasks.get(task_id)
        _log.info("runtime task cancel requested task_id=%s owner=%s status=%s", task_id, record.owner, record.status)
        self.scheduler.cancel(record)

    def write_runtime_task_stdin(self, task_id: str, text: str) -> None:
        from .tasks import write_task_stdin

        write_task_stdin(self.tasks.get(task_id), text)

    async def drain_pending_task_deliveries(self, conversation_id: str) -> None:
        await drain_pending_task_deliveries(self, conversation_id)

    def allow_task_deliveries(self, conversation_id: str) -> None:
        conversation = self.conversations.get_if_present(conversation_id)
        if conversation is not None:
            conversation.allow_task_deliveries()

    def suppress_task_deliveries(self, conversation_id: str) -> None:
        suppress_conversation_task_deliveries(self, conversation_id)

    def _schedule_task_delivery(self, record: RuntimeTaskRecord) -> None:
        _log.info(
            "terminal task delivery scheduled task_id=%s owner=%s status=%s delivery=%s",
            record.id,
            record.owner,
            record.status,
            record.delivery,
        )
        task = asyncio.create_task(self._deliver_terminal_task(record), name="task_delivery")
        self._task_delivery_tasks.add(task)
        task.add_done_callback(self._task_delivery_tasks.discard)

    async def _deliver_terminal_task(self, record: RuntimeTaskRecord) -> None:
        try:
            await schedule_task_delivery(self, record)
        except Exception:
            _log.exception(
                "terminal task delivery failed task_id=%s owner=%s",
                record.id,
                record.owner,
            )
            raise

    async def wait_until_terminal_or_timeout(self, task_id: str, timeout: float) -> None:
        await wait_until_terminal_or_timeout(self.tasks, task_id, timeout)

    def _resolve_workspace(self, actor_id: str) -> Path | None:
        if self.resolve_actor_workspace is not None:
            return self.resolve_actor_workspace(actor_id)
        actor = self.actors.get(actor_id)
        if actor is not None:
            return Path(actor.config.workspace).resolve()
        return None

    async def shutdown(self) -> None:
        _log.info(
            "runtime shutdown starting actors=%s integrations=%s tasks=%s detached_tasks=%s delivery_tasks=%s",
            len(self.actors),
            len(self.integrations),
            len(self.tasks.list()),
            len(self._detached_tasks),
            len(self._task_delivery_tasks),
        )
        await self.resource_supervisor.stop()
        await self.shares.stop_background_cleanup()
        self.cron.shutdown()
        await self.scheduler.shutdown()
        if self._task_delivery_tasks:
            await asyncio.gather(*self._task_delivery_tasks, return_exceptions=True)
            self._task_delivery_tasks.clear()
        await self.listeners.stop()
        await self.conversations.stop_background_cleanup()
        await self.conversations.close_all()
        if self._detached_tasks:
            await asyncio.gather(*self._detached_tasks, return_exceptions=True)
            self._detached_tasks.clear()
        for integration in reversed(list(self.integrations.values())):
            await integration.close()
        self.integrations.clear()
        for actor_id in list(self._actor_tasks):
            await self.stop_actor_task(actor_id)
        for actor in list(self.actors.values()):
            await actor.close()
        self.actors.clear()
        self.cache.clear()
        await self.db.close()
        _log.info("runtime shutdown complete data_dir=%s", self.data_dir)

    async def _run_actor_task(self, key: str, coro_factory: TaskCoroFactory) -> None:
        stdin = TextStream()
        stdout = TextStream()
        try:
            await coro_factory(stdin, stdout)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("actor task failed key=%s", key)
            raise
        finally:
            if self._actor_tasks.get(key) is asyncio.current_task():
                self._actor_tasks.pop(key, None)
                _log.info("actor task removed key=%s", key)
