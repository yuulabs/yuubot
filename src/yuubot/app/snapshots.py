"""Read-only view builders merging durable records with runtime state."""

import re
from typing import TYPE_CHECKING

import msgspec

from ..domain.messages import ModelCard
from ..domain.stream import StreamEvent
from ..domain.records import LifecycleError, RouteRecord
from ..llm.records import ProviderRecord
from ..llm.types import ProviderSnapshot
from ..runtime.events import RuntimeEvent
from ..runtime.tasks import RuntimeTaskRecord, task_record_snapshot

if TYPE_CHECKING:
    from .service import Yuubot

RUNTIME_EVENT_SNAPSHOT_LIMIT = 100
SECRET_FIELD_RE = re.compile(r"(api_)?key|token|secret|password", re.IGNORECASE)


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
    payload: dict[str, object]


class RuntimeActorView(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    status: str
    mailbox: str


class RuntimeIntegrationView(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    package_path: str


class TaskSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    owner: str
    kind: str
    name: str
    intro: str
    status: str
    error: str | None
    exit_code: int | None
    delivery_state: str
    stdout_tail: str = ""


class RuntimeSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    data_dir: str
    workspace_dir: str
    tasks: list[TaskSnapshot]
    actors: list[RuntimeActorView]
    integrations: list[RuntimeIntegrationView]
    events: list[RuntimeEventView]


class BootstrapSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    development: bool
    schema_version: int
    providers: list[ProviderSnapshot]
    actors: list[ActorSnapshot]
    integrations: list[IntegrationSnapshot]
    routes: list[RouteRecord]
    conversations: list[ConversationSummary]


def task_snapshot_from_record(record: RuntimeTaskRecord, *, include_stdout: bool = False) -> TaskSnapshot:
    raw = task_record_snapshot(record, include_stdout=include_stdout)
    return TaskSnapshot(
        id=str(raw["id"]),
        owner=str(raw["owner"]),
        kind=str(raw["kind"]),
        name=str(raw["name"]),
        intro=str(raw["intro"]),
        status=str(raw["status"]),
        error=raw["error"] if raw["error"] is None or isinstance(raw["error"], str) else str(raw["error"]),
        exit_code=raw["exit_code"] if raw["exit_code"] is None or isinstance(raw["exit_code"], int) else None,
        delivery_state=str(raw["delivery_state"]),
        stdout_tail=str(raw.get("stdout_tail", "")),
    )


async def bootstrap_snapshot(app: "Yuubot") -> BootstrapSnapshot:
    return BootstrapSnapshot(
        development=app.runtime.development,
        schema_version=await app.runtime.state.schema_version(),
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
                workspace=record.workspace or str(app.runtime.workspace_dir / record.id),
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
    return {
        key: "***" if SECRET_FIELD_RE.search(key) and value else value
        for key, value in config.items()
    }


def runtime_snapshot(app: "Yuubot") -> RuntimeSnapshot:
    events = list(app.runtime.eventbus.events)[-RUNTIME_EVENT_SNAPSHOT_LIMIT:]
    return RuntimeSnapshot(
        data_dir=str(app.runtime.data_dir),
        workspace_dir=str(app.runtime.workspace_dir),
        tasks=[task_snapshot_from_record(record) for record in app.runtime.tasks.list()],
        actors=[
            RuntimeActorView(id=actor.config.id, status=actor.status, mailbox=f"actor:{actor.config.id}")
            for actor in app.actors.values()
        ],
        integrations=[
            RuntimeIntegrationView(name=integration.name, package_path=integration.package_path)
            for integration in app.runtime.integrations.values()
        ],
        events=[_runtime_event_view(event) for event in events],
    )


def task_snapshot(app: "Yuubot", task_id: str, *, include_stdout: bool = False) -> TaskSnapshot:
    return task_snapshot_from_record(app.runtime.tasks.get(task_id), include_stdout=include_stdout)


async def _provider_snapshot(app: "Yuubot", record: ProviderRecord) -> ProviderSnapshot:
    cards = await app.runtime.state.list_model_cards(record.id)
    return app.provider_snapshot(record, cards)


def _runtime_event_view(event: RuntimeEvent) -> RuntimeEventView:
    payload = dict(event.payload)
    stream_event = payload.get("event")
    if isinstance(stream_event, StreamEvent):
        payload["event"] = msgspec.to_builtins(stream_event)
    return RuntimeEventView(ts=event.ts, kind=event.kind, payload=payload)
