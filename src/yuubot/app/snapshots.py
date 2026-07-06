"""Read-only view builders merging durable records with runtime state."""

from typing import TYPE_CHECKING

import msgspec

from ..domain.messages import ModelCard
from ..domain.stream import StreamEvent
from ..domain.records import LifecycleError, RouteRecord
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
SKIPPED_STREAM_KINDS = frozenset({"text_delta", "reasoning_delta", "tool_arguments_delta", "tool_arguments_end", "stream_stop"})


class ActorSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str
    enabled: bool
    status: str
    workspace: str
    provider: str
    model: ModelCard
    last_error: LifecycleError | None = None


class IntegrationSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    type: str
    name: str
    package_path: str
    enabled: bool
    configured: bool
    config_schema: dict[str, object]
    config: dict[str, object]
    last_error: LifecycleError | None = None


class ConversationSummary(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    actor_id: str = ""
    status: str = ""
    created_at: str = ""
    last_active_at: str | None = None
    title: str = ""
    message_count: int = 0
    last_seq: int | None = None
    last_error: dict[str, object] | None = None


class RuntimeEventView(msgspec.Struct, frozen=True, kw_only=True):
    ts: str
    kind: str
    title: str
    detail: str = ""
    context: dict[str, object] = msgspec.field(default_factory=dict)


class RuntimeActorView(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    status: str
    mailbox: str


class RuntimeIntegrationView(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    package_path: str

class RuntimeSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    data_dir: str
    workspace_dir: str
    host: HostStats
    tasks: list[TaskSnapshot]
    actors: list[RuntimeActorView]
    integrations: list[RuntimeIntegrationView]
    events: list[RuntimeEventView]


class BootstrapSnapshot(msgspec.Struct, frozen=True, kw_only=True):
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
        development=app.runtime.development,
        schema_version=await app.runtime.state.schema_version(),
        workspace_dir=str(app.runtime.workspace_dir),
        providers=[
            await _provider_snapshot(app, record)
            for record in sorted(app.provider_records.values(), key=lambda item: item.id)
        ],
        actors=await actor_snapshots(app),
        integrations=await integration_snapshots(app),
        routes=await app.list_routes(),
        conversations=await conversation_summaries(app),
    )


async def actor_snapshots(app: "Yuubot") -> list[ActorSnapshot]:
    statuses = await app.runtime.state.actor_statuses()
    snapshots: list[ActorSnapshot] = []
    for record in app.actor_records.values():
        live = app.actors.get(record.id)
        status = statuses.get(record.id)
        snapshots.append(
            ActorSnapshot(
                id=record.id,
                name=record.name,
                description=record.description,
                enabled=status.enabled if status is not None else live is not None,
                status=live.status if live is not None else (status.status if status is not None else "disabled"),
                last_error=status.last_error if status is not None else None,
                workspace=record.workspace or record.id,
                provider=record.provider,
                model=record.model,
            )
        )
    for actor_id, actor in app.actors.items():
        if actor_id in app.actor_records:
            continue
        snapshots.append(
            ActorSnapshot(
                id=actor_id,
                name=actor.config.name,
                description=actor.config.description,
                enabled=True,
                status=actor.status,
                workspace=actor.config.workspace,
                provider="",
                model=actor.config.model,
            )
        )
    return snapshots


async def conversation_summaries(app: "Yuubot") -> list[ConversationSummary]:
    by_id = {item["id"]: item for item in await app.runtime.history.list_conversations()}
    summaries: list[ConversationSummary] = []
    for record in await app.runtime.state.list_conversations():
        history = by_id.pop(record.id, {})
        summaries.append(
            ConversationSummary(
                id=record.id,
                actor_id=record.actor_id,
                status=record.status,
                created_at=record.created_at,
                last_active_at=record.last_active_at,
                title=record.title,
                last_error=record.last_error,
                message_count=int(history.get("message_count", 0)),
                last_seq=history.get("last_seq"),
            )
        )
    for item in by_id.values():
        summaries.append(
            ConversationSummary(
                id=str(item["id"]),
                message_count=int(item.get("message_count", 0)),
                last_seq=item.get("last_seq"),
                last_active_at=item.get("last_active_at"),
            )
        )
    return summaries


async def integration_snapshots(app: "Yuubot") -> list[IntegrationSnapshot]:
    statuses = await app.runtime.state.integration_statuses()
    snapshots: list[IntegrationSnapshot] = []
    for integration_type, spec in sorted(app.runtime.integration_registry.specs().items()):
        record = app.integration_records.get(integration_type)
        state = statuses.get(integration_type)
        snapshots.append(
            IntegrationSnapshot(
                type=integration_type,
                name=record.name if record is not None else integration_type,
                package_path=spec.package_path,
                enabled=state.enabled if state is not None else (record is not None and record.name in app.runtime.integrations),
                configured=record is not None,
                last_error=state.last_error if state is not None else None,
                config_schema=msgspec.json.schema(spec.config_type),
                config=redacted_integration_config(record.config if record is not None else {}),
            )
        )
    return snapshots


def redacted_integration_config(config: dict[str, object]) -> dict[str, object]:
    return redact_config(config)


def runtime_snapshot(app: "Yuubot") -> RuntimeSnapshot:
    events = [_runtime_event_view(event) for event in app.runtime.eventbus.events]
    visible_events = [event for event in events if event is not None][-RUNTIME_EVENT_SNAPSHOT_LIMIT:]
    host = app.runtime.resource_supervisor.host_stats or collect_host_stats(disk_path=app.runtime.data_dir)
    return RuntimeSnapshot(
        data_dir=str(app.runtime.data_dir),
        workspace_dir=str(app.runtime.workspace_dir),
        host=host,
        tasks=[task_record_snapshot(record) for record in app.runtime.tasks.list()],
        actors=[
            RuntimeActorView(id=actor.config.id, status=actor.status, mailbox=f"actor:{actor.config.id}")
            for actor in app.actors.values()
        ],
        integrations=[
            RuntimeIntegrationView(name=integration.name, package_path=integration.package_path)
            for integration in app.runtime.integrations.values()
        ],
        events=visible_events,
    )


def task_snapshot(app: "Yuubot", task_id: str, *, include_stdout: bool = False) -> TaskSnapshot:
    return task_record_snapshot(app.runtime.tasks.get(task_id), include_stdout=include_stdout)


async def _provider_snapshot(app: "Yuubot", record: ProviderRecord) -> ProviderSnapshot:
    cards = await app.runtime.state.list_model_cards(record.id)
    return app.provider_snapshot(record, cards)


def _runtime_event_view(event: RuntimeEvent) -> RuntimeEventView | None:
    if event.kind in SKIPPED_RUNTIME_EVENT_KINDS:
        return None

    payload = _runtime_event_payload(event)
    if event.kind == "conversation.stream":
        return _stream_event_view(event.ts, payload)

    title, detail = _runtime_event_copy(event.kind, payload)
    context = _runtime_event_context(event.kind, payload)
    return RuntimeEventView(ts=event.ts, kind=event.kind, title=title, detail=detail, context=context)


def _runtime_event_payload(event: RuntimeEvent) -> dict[str, object]:
    payload = dict(event.payload)
    stream_event = payload.get("event")
    if isinstance(stream_event, StreamEvent):
        payload["event"] = msgspec.to_builtins(stream_event)
    return payload


def _stream_event_view(ts: str, payload: dict[str, object]) -> RuntimeEventView | None:
    stream_event = payload.get("event")
    if not isinstance(stream_event, dict):
        return None
    stream_kind = stream_event.get("kind")
    if not isinstance(stream_kind, str) or stream_kind in SKIPPED_STREAM_KINDS:
        return None
    event_payload = stream_event.get("payload")
    context = _copy_keys(payload, "conversation_id")
    if isinstance(event_payload, dict):
        context |= _compact_payload(event_payload)
    if stream_kind == "tool_name":
        name = event_payload.get("name") if isinstance(event_payload, dict) else None
        detail = str(name) if isinstance(name, str) and name else "Tool call"
        return RuntimeEventView(ts=ts, kind="conversation.tool_call", title="Tool call requested", detail=detail, context=context)
    return RuntimeEventView(ts=ts, kind=f"conversation.stream.{stream_kind}", title=_humanize_kind(stream_kind), context=context)


def _runtime_event_copy(kind: str, payload: dict[str, object]) -> tuple[str, str]:
    if kind == "conversation.input":
        return ("Turn started", "User message accepted")
    if kind == "conversation.output":
        reason = _optional_str(payload.get("reason"), "unknown")
        return ("Turn finished", f"Reason: {reason}")
    if kind == "conversation.tool_results":
        count = _optional_int(payload.get("count"))
        return ("Tool results ready", _count_detail(count, "result"))
    if kind == "conversation.cost":
        tokens = sum(_optional_int(payload.get(key)) or 0 for key in ("input_tokens", "cached_input_tokens", "output_tokens"))
        cost = _optional_float(payload.get("payg_cost"))
        if cost is not None:
            return ("Cost recorded", f"{tokens} tokens / ${cost:.6f}")
        return ("Cost recorded", f"{tokens} tokens")
    if kind == "task.started":
        return ("Task started", _optional_str(payload.get("name"), _optional_str(payload.get("task_id"), "")))
    if kind == "task.finished":
        status = _optional_str(payload.get("status"), "finished")
        name = _optional_str(payload.get("name"), _optional_str(payload.get("task_id"), ""))
        return ("Task finished", f"{name} / {status}" if name else status)
    if kind == "actor.output":
        outputs = _optional_int(payload.get("outputs"))
        return ("Actor replied", _count_detail(outputs, "output"))
    if kind == "actor.busy":
        return ("Actor busy", "Conversation is already running")
    if kind == "actor.blocked":
        return ("Actor blocked", _optional_str(payload.get("reason"), "Blocked"))
    if kind == "incoming.message":
        return ("Inbound message received", _optional_str(payload.get("route"), ""))
    if kind == "gateway.dispatch":
        delivered = payload.get("delivered")
        return ("Inbound message dispatched", "Delivered" if delivered is True else "Not delivered")
    if kind == "wakeup.delivered":
        return ("Wakeup delivered", _optional_str(payload.get("inbound_kind"), ""))
    if kind == "cron.started":
        return ("Cron job started", _optional_str(payload.get("job_id"), ""))
    if kind == "cron.finished":
        return ("Cron job finished", _optional_str(payload.get("job_id"), ""))
    if kind == "cron.failed":
        return ("Cron job failed", _optional_str(payload.get("job_id"), ""))
    if kind == "share.created":
        return ("Share created", _optional_str(payload.get("source_path"), ""))
    if kind == "share.revoked":
        return ("Share revoked", _optional_str(payload.get("share_id"), ""))
    if kind == "share.expired":
        return ("Share expired", _optional_str(payload.get("share_id"), ""))
    if kind == "resource.disk_warning":
        pct = _optional_float(payload.get("disk_percent"))
        free = _optional_int(payload.get("disk_free_bytes"))
        detail = f"{pct:.1f}% used" if pct is not None else "disk usage high"
        if free is not None:
            detail = f"{detail}; {free} bytes free"
        return ("Disk space warning", detail)
    if kind == "resource.disk_critical":
        pct = _optional_float(payload.get("disk_percent"))
        free = _optional_int(payload.get("disk_free_bytes"))
        detail = f"{pct:.1f}% used" if pct is not None else "disk usage critical"
        if free is not None:
            detail = f"{detail}; {free} bytes free"
        return ("Disk space critical", detail)
    if kind == "resource.disk_ok":
        pct = _optional_float(payload.get("disk_percent"))
        detail = f"{pct:.1f}% used" if pct is not None else "disk usage recovered"
        return ("Disk space recovered", detail)
    return (_humanize_kind(kind), "")


def _runtime_event_context(kind: str, payload: dict[str, object]) -> dict[str, object]:
    if kind.startswith("conversation."):
        return _copy_keys(
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
        return _copy_keys(payload, "task_id", "owner", "kind", "name", "status", "exit_code")
    return _compact_payload(payload)


def _copy_keys(payload: dict[str, object], *keys: str) -> dict[str, object]:
    return {key: payload[key] for key in keys if key in payload and _is_compact_value(payload[key])}


def _compact_payload(payload: dict[str, object]) -> dict[str, object]:
    compact: dict[str, object] = {}
    for key, value in payload.items():
        if len(compact) >= RUNTIME_EVENT_CONTEXT_LIMIT:
            break
        if _is_compact_value(value):
            compact[key] = value
    return compact


def _is_compact_value(value: object) -> bool:
    if value is None or isinstance(value, bool | int | float):
        return True
    return isinstance(value, str) and len(value) <= 160


def _optional_str(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _count_detail(count: int | None, noun: str) -> str:
    if count is None:
        return ""
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _humanize_kind(kind: str) -> str:
    return kind.replace(".", " ").replace("_", " ").title()
