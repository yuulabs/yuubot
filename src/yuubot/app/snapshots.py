"""Read-only view builders merging durable records with runtime state."""

import re
from typing import TYPE_CHECKING

import msgspec

from ..llm.records import ProviderRecord
from ..runtime.tasks import task_record_snapshot

if TYPE_CHECKING:
    from .service import Yuubot

RUNTIME_EVENT_SNAPSHOT_LIMIT = 100
SECRET_FIELD_RE = re.compile(r"(api_)?key|token|secret|password", re.IGNORECASE)


async def bootstrap_snapshot(app: "Yuubot") -> dict[str, object]:
    return {
        "development": app.runtime.development,
        "schema_version": await app.runtime.state.schema_version(),
        "providers": [
            await _provider_snapshot(app, record)
            for record in sorted(app.provider_records.values(), key=lambda item: item.id)
        ],
        "actors": await actor_snapshots(app),
        "integrations": await integration_snapshots(app),
        "routes": [msgspec.to_builtins(record) for record in await app.list_routes()],
        "conversations": await conversation_summaries(app),
    }


async def actor_snapshots(app: "Yuubot") -> list[dict[str, object]]:
    statuses = await app.runtime.state.actor_statuses()
    snapshots: list[dict[str, object]] = []
    for record in app.actor_records.values():
        live = app.actors.get(record.id)
        status = statuses.get(record.id, {})
        snapshots.append(
            {
                "id": record.id,
                "name": record.name,
                "description": record.description,
                "enabled": bool(status.get("enabled", live is not None)),
                "status": live.status if live is not None else status.get("status", "disabled"),
                "last_error": status.get("last_error"),
                "workspace": record.workspace or str(app.runtime.workspace_dir / record.id),
                "provider": record.provider,
                "model": msgspec.to_builtins(record.model),
            }
        )
    for actor_id, actor in app.actors.items():
        if actor_id in app.actor_records:
            continue
        snapshots.append(
            {
                "id": actor_id,
                "name": actor.config.name,
                "description": actor.config.description,
                "enabled": True,
                "status": actor.status,
                "last_error": None,
                "workspace": actor.config.workspace,
                "provider": "",
                "model": msgspec.to_builtins(actor.config.model),
            }
        )
    return snapshots


async def conversation_summaries(app: "Yuubot") -> list[dict[str, object]]:
    by_id = {item["id"]: dict(item) for item in await app.runtime.history.list_conversations()}
    summaries: list[dict[str, object]] = []
    for record in await app.runtime.state.list_conversations():
        history = by_id.pop(record["id"], {})
        merged = dict(record)
        merged["message_count"] = history.get("message_count", 0)
        merged["last_seq"] = history.get("last_seq")
        summaries.append(merged)
    summaries.extend(by_id.values())
    return summaries


async def integration_snapshots(app: "Yuubot") -> list[dict[str, object]]:
    statuses = await app.runtime.state.integration_statuses()
    snapshots: list[dict[str, object]] = []
    for integration_type, spec in sorted(app.runtime.integration_registry.specs().items()):
        record = app.integration_records.get(integration_type)
        state = statuses.get(integration_type, {})
        snapshots.append(
            {
                "type": integration_type,
                "name": record.name if record is not None else integration_type,
                "package_path": spec.package_path,
                "enabled": bool(state.get("enabled", record is not None and record.name in app.runtime.integrations)),
                "configured": record is not None,
                "last_error": state.get("last_error"),
                "config_schema": msgspec.json.schema(spec.config_type),
                "config": redacted_integration_config(record.config if record is not None else {}),
            }
        )
    return snapshots


def redacted_integration_config(config: dict[str, object]) -> dict[str, object]:
    return {
        key: "***" if SECRET_FIELD_RE.search(key) and value else value
        for key, value in config.items()
    }


def runtime_snapshot(app: "Yuubot") -> dict[str, object]:
    events = list(app.runtime.eventbus.events)[-RUNTIME_EVENT_SNAPSHOT_LIMIT:]
    return {
        "data_dir": str(app.runtime.data_dir),
        "workspace_dir": str(app.runtime.workspace_dir),
        "tasks": [task_record_snapshot(record) for record in app.runtime.tasks.list()],
        "actors": [
            {"id": actor.config.id, "status": actor.status, "mailbox": f"actor:{actor.config.id}"}
            for actor in app.actors.values()
        ],
        "integrations": [
            {"name": integration.name, "package_path": integration.package_path}
            for integration in app.runtime.integrations.values()
        ],
        "events": [{"ts": event.ts, "kind": event.kind, "payload": event.payload} for event in events],
    }


def task_snapshot(app: "Yuubot", task_id: str, *, include_stdout: bool = False) -> dict[str, object]:
    return task_record_snapshot(app.runtime.tasks.get(task_id), include_stdout=include_stdout)


async def _provider_snapshot(app: "Yuubot", record: ProviderRecord) -> dict[str, object]:
    cards = await app.runtime.state.list_model_cards(record.id)
    return app.provider_snapshot(record, cards)
