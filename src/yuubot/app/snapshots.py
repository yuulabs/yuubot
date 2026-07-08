"""Read-only view builders merging durable records with runtime state."""

from typing import TYPE_CHECKING

import msgspec

from ..domain.messages import ModelCard
from ..domain.stream import StreamEvent, ToolNamePayload, Usage
from ..domain.records import ActorRecord, ActorStatus, IntegrationStatus, LifecycleError, RouteRecord
from ..runtime.event_payloads import (
    ActorBlockedPayload,
    ActorBusyPayload,
    ActorContextCompactedPayload,
    ActorContextCompactionStoppedPayload,
    ActorOutputPayload,
    ConversationCostPayload,
    ConversationInputPayload,
    ConversationOutputPayload,
    ConversationStreamPayload,
    ConversationToolResultsPayload,
    CronFailedPayload,
    CronFinishedPayload,
    CronStartedPayload,
    GatewayDispatchPayload,
    IncomingMessagePayload,
    ResourceDiskCriticalPayload,
    ResourceDiskOkPayload,
    ResourceDiskWarningPayload,
    RuntimeEventPayload,
    ShareCreatedPayload,
    ShareExpiredPayload,
    ShareRevokedPayload,
    TaskFinishedPayload,
    TaskStartedPayload,
    WakeupDeliveredPayload,
)
from ..integrations import IntegrationRecord, integration_health
from ..integrations.registry import IntegrationSpec
from ..actor.lifecycle import Actor
from ..llm.records import ProviderRecord
from ..llm.types import ProviderSnapshot
from ..runtime.host_stats import HostStats, collect_host_stats
from ..runtime.events import RuntimeEvent
from ..runtime.tasks import TaskSnapshot, task_record_snapshot
from ..util.secrets import redact_config

if TYPE_CHECKING:
    from .service import Yuubot

RUNTIME_EVENT_SNAPSHOT_LIMIT = 100
RUNTIME_EVENT_CONTEXT_LIMIT = 8
SKIPPED_RUNTIME_EVENT_KINDS = frozenset({"conversation.history.append"})
SKIPPED_STREAM_KINDS = frozenset(
    {"text_delta", "reasoning_delta", "tool_arguments_delta", "tool_arguments_end", "tool_result_delta", "stream_stop"}
)


class ActorSnapshot(msgspec.Struct, frozen=True):
    id: str
    name: str
    description: str
    enabled: bool
    status: str
    workspace: str
    provider: str
    model: ModelCard
    context_compression_tokens: int
    last_error: LifecycleError | None = None


class IntegrationSnapshot(msgspec.Struct, frozen=True):
    type: str
    name: str
    package_path: str
    enabled: bool
    configured: bool
    config_schema: dict[str, object]
    config: dict[str, object]
    last_error: LifecycleError | None = None
    health_status: str = ""
    health_reason: str = ""
    health_details: dict[str, object] = msgspec.field(default_factory=dict)
    action_hint: dict[str, object] | None = None


class ConversationSummary(msgspec.Struct, frozen=True):
    id: str
    actor_id: str = ""
    status: str = ""
    created_at: str = ""
    last_active_at: str | None = None
    title: str = ""
    message_count: int = 0
    last_seq: int | None = None
    last_error: dict[str, object] | None = None
    last_input_tokens: int = 0
    last_cached_input_tokens: int = 0
    last_output_tokens: int = 0


class RuntimeEventView(msgspec.Struct, frozen=True):
    ts: str
    kind: str
    title: str
    detail: str = ""
    context: dict[str, object] = msgspec.field(default_factory=dict)


class RuntimeActorView(msgspec.Struct, frozen=True):
    id: str
    status: str
    mailbox: str


class RuntimeIntegrationView(msgspec.Struct, frozen=True):
    name: str
    package_path: str

class RuntimeSnapshot(msgspec.Struct, frozen=True):
    data_dir: str
    workspace_dir: str
    host: HostStats
    tasks: list[TaskSnapshot]
    actors: list[RuntimeActorView]
    integrations: list[RuntimeIntegrationView]
    events: list[RuntimeEventView]


class BootstrapSnapshot(msgspec.Struct, frozen=True):
    development: bool
    schema_version: int
    workspace_dir: str
    providers: list[ProviderSnapshot]
    actors: list[ActorSnapshot]
    integrations: list[IntegrationSnapshot]
    routes: list[RouteRecord]
    conversations: list[ConversationSummary]

async def bootstrap_snapshot(app: "Yuubot") -> BootstrapSnapshot:
    return BootstrapSnapshot(
        app.runtime.development,
        await app.runtime.state.schema_version(),
        str(app.runtime.workspace_dir),
        [
            await _provider_snapshot(app, record)
            for record in sorted(app.provider_records.values(), key=lambda item: item.id)
        ],
        await actor_snapshots(app),
        await integration_snapshots(app),
        await app.list_routes(),
        await conversation_summaries(app),
    )


async def actor_snapshot(app: "Yuubot", actor_id: str) -> ActorSnapshot | None:
    statuses = await app.runtime.state.actor_statuses()
    record = app.actor_records.get(actor_id)
    if record is not None:
        return _actor_snapshot(record, app.actors.get(actor_id), statuses.get(actor_id))
    live = app.actors.get(actor_id)
    if live is None:
        return None
    return ActorSnapshot(
        actor_id,
        live.config.name,
        live.config.description,
        True,
        live.status,
        live.config.workspace,
        "",
        live.config.model,
        live.config.context_compression_tokens,
    )


async def actor_snapshots(app: "Yuubot") -> list[ActorSnapshot]:
    statuses = await app.runtime.state.actor_statuses()
    snapshots: list[ActorSnapshot] = []
    for record in app.actor_records.values():
        snapshots.append(_actor_snapshot(record, app.actors.get(record.id), statuses.get(record.id)))
    for actor_id, actor in app.actors.items():
        if actor_id in app.actor_records:
            continue
        snapshots.append(
            ActorSnapshot(
                actor_id,
                actor.config.name,
                actor.config.description,
                True,
                actor.status,
                actor.config.workspace,
                "",
                actor.config.model,
                actor.config.context_compression_tokens,
            )
        )
    return snapshots


def _actor_snapshot(record: ActorRecord, live: Actor | None, status: ActorStatus | None) -> ActorSnapshot:
    return ActorSnapshot(
        record.id,
        record.name,
        record.description,
        status.enabled if status is not None else live is not None,
        live.status if live is not None else (status.status if status is not None else "disabled"),
        last_error=status.last_error if status is not None else None,
        workspace=record.workspace or record.id,
        provider=record.provider,
        model=record.model,
        context_compression_tokens=record.context_compression_tokens,
    )


async def conversation_summary(app: "Yuubot", conversation_id: str) -> ConversationSummary | None:
    record = await app.runtime.state.get_conversation(conversation_id)
    history = await app.runtime.history.conversation_meta(conversation_id)
    if record is None and history is None:
        return None
    if record is not None:
        usage = await _last_cost_usage(app, record.id)
        return ConversationSummary(
            record.id,
            record.actor_id,
            record.status,
            record.created_at,
            record.last_active_at,
            record.title,
            last_error=record.last_error,
            message_count=history.message_count if history is not None else 0,
            last_seq=history.last_seq if history is not None else None,
            last_input_tokens=usage.input_tokens,
            last_cached_input_tokens=usage.cached_input_tokens,
            last_output_tokens=usage.output_tokens,
        )
    usage = await _last_cost_usage(app, conversation_id)
    return ConversationSummary(
        history.id,
        message_count=history.message_count,
        last_seq=history.last_seq,
        last_active_at=history.last_active_at,
        last_input_tokens=usage.input_tokens,
        last_cached_input_tokens=usage.cached_input_tokens,
        last_output_tokens=usage.output_tokens,
    )


async def conversation_summaries(app: "Yuubot") -> list[ConversationSummary]:
    by_id = {item.id: item for item in await app.runtime.history.list_conversations()}
    summaries: list[ConversationSummary] = []
    for record in await app.runtime.state.list_conversations():
        history = by_id.pop(record.id, None)
        usage = await _last_cost_usage(app, record.id)
        summaries.append(
            ConversationSummary(
                record.id,
                record.actor_id,
                record.status,
                record.created_at,
                record.last_active_at,
                record.title,
                last_error=record.last_error,
                message_count=history.message_count if history is not None else 0,
                last_seq=history.last_seq if history is not None else None,
                last_input_tokens=usage.input_tokens,
                last_cached_input_tokens=usage.cached_input_tokens,
                last_output_tokens=usage.output_tokens,
            )
        )
    for item in by_id.values():
        usage = await _last_cost_usage(app, item.id)
        summaries.append(
            ConversationSummary(
                item.id,
                message_count=item.message_count,
                last_seq=item.last_seq,
                last_active_at=item.last_active_at,
                last_input_tokens=usage.input_tokens,
                last_cached_input_tokens=usage.cached_input_tokens,
                last_output_tokens=usage.output_tokens,
            )
        )
    return summaries


async def _last_cost_usage(app: "Yuubot", conversation_id: str) -> Usage:
    costs = await app.runtime.state.load_costs(conversation_id)
    if not costs:
        return Usage()
    return costs[-1].usage


async def integration_snapshot(app: "Yuubot", integration_type: str) -> IntegrationSnapshot | None:
    spec = app.runtime.integration_registry.specs().get(integration_type)
    if spec is None:
        return None
    statuses = await app.runtime.state.integration_statuses()
    return await _integration_snapshot(
        app,
        integration_type,
        spec,
        app.integration_records.get(integration_type),
        statuses.get(integration_type),
    )


async def integration_snapshots(app: "Yuubot") -> list[IntegrationSnapshot]:
    statuses = await app.runtime.state.integration_statuses()
    snapshots: list[IntegrationSnapshot] = []
    for integration_type, spec in sorted(app.runtime.integration_registry.specs().items()):
        snapshot = await _integration_snapshot(
            app,
            integration_type,
            spec,
            app.integration_records.get(integration_type),
            statuses.get(integration_type),
        )
        snapshots.append(snapshot)
    return snapshots


async def _integration_snapshot(
    app: "Yuubot",
    integration_type: str,
    spec: IntegrationSpec,
    record: IntegrationRecord | None,
    state: IntegrationStatus | None,
) -> IntegrationSnapshot:
    default_config = app.runtime.integration_registry.default_config(integration_type)
    enabled = state.enabled if state is not None else (record is not None and record.name in app.runtime.integrations)
    live = app.runtime.integrations.get(record.name if record is not None else integration_type)
    health = await integration_health(live) if enabled and live is not None else None
    health_status = health.status if health is not None else ("disabled" if not enabled else ("error" if state is not None and state.last_error is not None else "ready"))
    health_reason = health.reason if health is not None else (state.last_error.message if state is not None and state.last_error is not None else "")
    return IntegrationSnapshot(
        integration_type,
        record.name if record is not None else integration_type,
        spec.package_path,
        enabled,
        record is not None or default_config is not None,
        last_error=state.last_error if state is not None else None,
        config_schema=msgspec.json.schema(spec.config_type),
        config=redacted_integration_config(record.config if record is not None else default_config or {}),
        health_status=health_status,
        health_reason=health_reason,
        health_details=health.details if health is not None else {},
        action_hint=health.action_hint if health is not None else None,
    )


def redacted_integration_config(config: dict[str, object]) -> dict[str, object]:
    return redact_config(config)


def runtime_snapshot(app: "Yuubot") -> RuntimeSnapshot:
    events = [_runtime_event_view(event) for event in app.runtime.eventbus.events]
    visible_events = [event for event in events if event is not None][-RUNTIME_EVENT_SNAPSHOT_LIMIT:]
    host = app.runtime.resource_supervisor.host_stats or collect_host_stats(app.runtime.data_dir)
    return RuntimeSnapshot(
        str(app.runtime.data_dir),
        str(app.runtime.workspace_dir),
        host,
        [task_record_snapshot(record) for record in app.runtime.tasks.list()],
        [
            RuntimeActorView(actor.config.id, actor.status, f"actor:{actor.config.id}")
            for actor in app.actors.values()
        ],
        [
            RuntimeIntegrationView(integration.name, integration.package_path)
            for integration in app.runtime.integrations.values()
        ],
        visible_events,
    )


def task_snapshot(app: "Yuubot", task_id: str, include_stdout: bool = False) -> TaskSnapshot:
    return task_record_snapshot(app.runtime.tasks.get(task_id), include_stdout)


async def _provider_snapshot(app: "Yuubot", record: ProviderRecord) -> ProviderSnapshot:
    cards = await app.runtime.state.list_model_cards(record.id)
    return app.provider_snapshot(record, cards)


def _runtime_event_view(event: RuntimeEvent) -> RuntimeEventView | None:
    if event.kind in SKIPPED_RUNTIME_EVENT_KINDS:
        return None

    payload = event.payload
    if isinstance(payload, ConversationStreamPayload):
        return _stream_event_view(event.ts, payload)

    title, detail = _runtime_event_copy(event.kind, payload)
    context = _runtime_event_context(event.kind, payload)
    return RuntimeEventView(event.ts, event.kind, title, detail, context)


def _stream_event_view(ts: str, payload: ConversationStreamPayload) -> RuntimeEventView | None:
    stream_event = payload.event
    stream_kind = stream_event.kind
    if stream_kind in SKIPPED_STREAM_KINDS:
        return None
    context = _compact_context({"conversation_id": payload.conversation_id})
    event_payload = stream_event.payload
    if isinstance(event_payload, ToolNamePayload):
        context |= _compact_context({"name": event_payload.name, "id": event_payload.id})
        detail = event_payload.name or "Tool call"
        return RuntimeEventView(ts, "conversation.tool_call", "Tool call requested", detail, context)
    if isinstance(event_payload, msgspec.Struct):
        context |= _compact_context(msgspec.to_builtins(event_payload))
    return RuntimeEventView(ts, f"conversation.stream.{stream_kind}", _humanize_kind(stream_kind), context=context)


def _runtime_event_copy(kind: str, payload: RuntimeEventPayload) -> tuple[str, str]:
    if isinstance(payload, ConversationInputPayload):
        return ("Turn started", "User message accepted")
    if isinstance(payload, ConversationOutputPayload):
        return ("Turn finished", f"Reason: {payload.reason or 'unknown'}")
    if isinstance(payload, ConversationToolResultsPayload):
        return ("Tool results ready", _count_detail(payload.count, "result"))
    if isinstance(payload, ConversationCostPayload):
        tokens = payload.input_tokens + payload.cached_input_tokens + payload.output_tokens
        if payload.estimated:
            return ("Cost recorded", f"{tokens} tokens")
        return ("Cost recorded", f"{tokens} tokens / ${payload.payg_cost:.6f}")
    if isinstance(payload, TaskStartedPayload):
        return ("Task started", payload.name or payload.task_id)
    if isinstance(payload, TaskFinishedPayload):
        name = payload.name or payload.task_id
        return ("Task finished", f"{name} / {payload.status}" if name else payload.status)
    if isinstance(payload, ActorOutputPayload):
        return ("Actor replied", _count_detail(payload.outputs, "output"))
    if isinstance(payload, ActorBusyPayload):
        return ("Actor busy", "Conversation is already running")
    if isinstance(payload, ActorBlockedPayload):
        return ("Actor blocked", payload.reason or "Blocked")
    if isinstance(payload, ActorContextCompactedPayload):
        return (
            "Actor context compacted",
            f"{payload.input_tokens}/{payload.threshold} input tokens",
        )
    if isinstance(payload, ActorContextCompactionStoppedPayload):
        return (
            "Actor compaction stopped",
            f"{payload.input_tokens}/{payload.threshold} input tokens",
        )
    if isinstance(payload, IncomingMessagePayload):
        return ("Inbound message received", payload.route)
    if isinstance(payload, GatewayDispatchPayload):
        return ("Inbound message dispatched", "Delivered" if payload.delivered else "Not delivered")
    if isinstance(payload, WakeupDeliveredPayload):
        return ("Wakeup delivered", payload.inbound_kind)
    if isinstance(payload, CronStartedPayload):
        return ("Cron job started", payload.job_id)
    if isinstance(payload, CronFinishedPayload):
        return ("Cron job finished", payload.job_id)
    if isinstance(payload, CronFailedPayload):
        return ("Cron job failed", payload.job_id)
    if isinstance(payload, ShareCreatedPayload):
        return ("Share created", payload.source_path)
    if isinstance(payload, ShareRevokedPayload):
        return ("Share revoked", payload.share_id)
    if isinstance(payload, ShareExpiredPayload):
        return ("Share expired", payload.share_id)
    if isinstance(payload, ResourceDiskWarningPayload):
        return ("Disk space warning", _disk_detail(payload.disk_percent, payload.disk_free_bytes, "high"))
    if isinstance(payload, ResourceDiskCriticalPayload):
        return ("Disk space critical", _disk_detail(payload.disk_percent, payload.disk_free_bytes, "critical"))
    if isinstance(payload, ResourceDiskOkPayload):
        return ("Disk space recovered", f"{payload.disk_percent:.1f}% used")
    return (_humanize_kind(kind), "")


def _runtime_event_context(kind: str, payload: RuntimeEventPayload) -> dict[str, object]:
    if kind.startswith("conversation."):
        return _context_from_payload(
            payload,
            "conversation_id",
            "reason",
            "count",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "payg_cost",
            "estimated",
        )
    if kind.startswith("task."):
        return _context_from_payload(payload, "task_id", "owner", "kind", "name", "status", "exit_code")
    if kind.startswith("actor.context_"):
        return _context_from_payload(
            payload,
            "actor_id",
            "old_conversation_id",
            "new_conversation_id",
            "conversation_id",
            "input_tokens",
            "threshold",
        )
    return _compact_context(msgspec.to_builtins(payload))


def _context_from_payload(payload: RuntimeEventPayload, *keys: str) -> dict[str, object]:
    raw = msgspec.to_builtins(payload)
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in keys if key in raw and _is_compact_value(raw[key])}


def _compact_context(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    compact: dict[str, object] = {}
    for key, value in payload.items():
        if len(compact) >= RUNTIME_EVENT_CONTEXT_LIMIT:
            break
        if _is_compact_value(value):
            compact[key] = value
    return compact


def _disk_detail(disk_percent: float, disk_free_bytes: int, label: str) -> str:
    detail = f"{disk_percent:.1f}% used"
    if label != "high":
        detail = f"{detail}; disk usage {label}"
    return f"{detail}; {disk_free_bytes} bytes free"


def _is_compact_value(value: object) -> bool:
    if value is None or isinstance(value, bool | int | float):
        return True
    return isinstance(value, str) and len(value) <= 160


def _count_detail(count: int | None, noun: str) -> str:
    if count is None:
        return ""
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _humanize_kind(kind: str) -> str:
    return kind.replace(".", " ").replace("_", " ").title()
