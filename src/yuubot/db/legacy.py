from __future__ import annotations

import base64
import binascii
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import msgspec
import yaml

from yuubot.integrations.records import IntegrationRecord
from yuubot.domain.messages import ModelCard
from yuubot.llm.records import ProviderRecord
from yuubot.domain.messages import ContentItem, GenText, HistoryToolSpecs, InputMessage, ModelCard, ToolResult
from yuubot.domain.records import ActorRecord, RouteRecord

from .database import Database
from .migrate import current_version, pending_versions

LEGACY_TABLES = (
    "llm_backends",
    "integrations",
    "capability_sets",
    "actors",
    "actor_ingress_rules",
    "conversations",
    "conversation_messages",
    "conversation_history_items",
)


class LegacyImportError(RuntimeError):
    pass


async def migrate_legacy(
    db: Database,
    *,
    data_dir: Path,
    legacy_db: Path | None,
    old_config: Path | None = None,
    dry_run: bool = False,
    force_import: bool = False,
) -> dict[str, object]:
    old_config_info = _old_config_info(old_config)
    source = legacy_db or _legacy_db_from_config(old_config_info) or auto_legacy_db(data_dir)
    report = await inspect_legacy(
        db,
        data_dir=data_dir,
        legacy_db=source,
        old_config_info=old_config_info,
    )
    if dry_run or source is None:
        return report
    if not report["legacy_detected"]:
        return report
    if await _already_imported(db, source):
        report["already_imported"] = True
        return report
    if db.path.exists() and not force_import and await _has_application_rows(db):
        raise LegacyImportError("new database already contains application rows; pass --force-import to import legacy data")

    backup_path = _backup_legacy_db(source)
    report["backup_path"] = str(backup_path)
    imported = await _import(db, data_dir=data_dir, legacy_db=source, old_config_info=old_config_info, report=report)
    await db.execute(
        "insert or replace into legacy_imports (source_path, imported_at, report) values (?, ?, ?)",
        (str(source), _now(), msgspec.json.encode(imported)),
    )
    await db.execute("insert or replace into app_meta (key, value) values ('legacy_imported_from', ?)", (str(source),))
    await db.commit()
    imported["already_imported"] = False
    return imported


async def inspect_legacy(
    db: Database | None,
    *,
    data_dir: Path,
    legacy_db: Path | None,
    old_config_info: dict[str, object] | None = None,
) -> dict[str, object]:
    schema_version = await current_version(db) if db is not None else 0
    pending = await pending_versions(db) if db is not None else []
    report: dict[str, object] = {
        "legacy_detected": legacy_db is not None and legacy_db.exists(),
        "legacy_db": str(legacy_db) if legacy_db is not None else None,
        "schema_version": schema_version,
        "pending_migrations": pending,
        "tables": {},
        "missing_tables": [],
        "records": {},
        "manual_actions": [],
        "dropped_metadata": [],
        "unconverted_history_path": str(data_dir / "legacy_unconverted_history.jsonl"),
        "already_imported": False,
    }
    if legacy_db is None or not legacy_db.exists():
        return report
    if db is not None:
        report["already_imported"] = await _already_imported(db, legacy_db)
    async with aiosqlite.connect(legacy_db) as old:
        existing = await _table_names(old)
        missing = [table for table in LEGACY_TABLES if table not in existing]
        report["missing_tables"] = missing
        counts: dict[str, int] = {}
        for table in LEGACY_TABLES:
            if table not in existing:
                counts[table] = 0
                continue
            cursor = await old.execute(f'select count(*) from "{table}"')
            row = await cursor.fetchone()
            counts[table] = int(row[0]) if row is not None else 0
        report["tables"] = counts
        report["records"] = {
            "llms": counts["llm_backends"],
            "integrations": counts["integrations"],
            "capability_sets": counts["capability_sets"],
            "actors": counts["actors"],
            "routes": counts["actor_ingress_rules"],
            "conversations": counts["conversations"],
            "conversation_messages": counts["conversation_messages"],
            "conversation_history_items": counts["conversation_history_items"],
        }
    if old_config_info and old_config_info.get("master_key"):
        report["old_config_master_key"] = "present"
    return report


def auto_legacy_db(data_dir: Path) -> Path | None:
    candidate = data_dir / "yuubot" / "yuubot.db"
    return candidate if candidate.exists() else None


def _old_config_info(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise LegacyImportError("old config must be a mapping")
    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
    database = data.get("database") if isinstance(data.get("database"), dict) else {}
    secrets = data.get("secrets") if isinstance(data.get("secrets"), dict) else {}
    return {
        "data_dir": str(paths.get("data_dir") or "") if isinstance(paths, dict) else "",
        "database_path": str(database.get("path") or "") if isinstance(database, dict) else "",
        "master_key": str(secrets.get("master_key") or "") if isinstance(secrets, dict) else "",
    }


def _legacy_db_from_config(info: dict[str, object]) -> Path | None:
    raw_path = str(info.get("database_path") or "")
    if raw_path and raw_path != ":memory:":
        return Path(raw_path).expanduser()
    raw_data_dir = str(info.get("data_dir") or "")
    if raw_data_dir:
        return Path(raw_data_dir).expanduser() / "yuubot" / "yuubot.db"
    return None


async def _import(
    db: Database,
    *,
    data_dir: Path,
    legacy_db: Path,
    old_config_info: dict[str, object],
    report: dict[str, object],
) -> dict[str, object]:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "workspace").mkdir(parents=True, exist_ok=True)
    unconverted_path = data_dir / "legacy_unconverted_history.jsonl"
    if unconverted_path.exists():
        unconverted_path.unlink()

    async with aiosqlite.connect(legacy_db) as old:
        old.row_factory = aiosqlite.Row
        tables = await _table_names(old)
        capabilities = {row["id"]: dict(row) for row in await _rows(old, "capability_sets", tables)}
        await _import_llms(db, old, tables, report)
        await _import_integrations(db, old, tables, old_config_info, report)
        await _import_actors(db, old, tables, capabilities, data_dir, _old_data_dir(old_config_info, legacy_db), report)
        await _import_routes(db, old, tables, report)
        await _import_conversations(db, old, tables, report)
        history_counts = await _import_history(db, old, tables, unconverted_path, report)
        await _import_messages(db, old, tables, history_counts, report)
    await db.commit()
    return report


async def _import_llms(db: Database, old: aiosqlite.Connection, tables: set[str], report: dict[str, object]) -> None:
    for row in await _rows(old, "llm_backends", tables):
        data = dict(row)
        model_configs = _json_obj(data.get("model_configs"), {})
        model = next(iter(model_configs), "default")
        provider_options = _json_obj(data.get("provider_options"), {})
        endpoint = _string(provider_options.get("base_url") or provider_options.get("endpoint"))
        name = _string(data.get("name") or data.get("id"))
        api_key_ref = _legacy_secret_ref(name) if _contains_secret(provider_options) else ""
        if api_key_ref:
            _append(report, "manual_actions", {"type": "llm_api_key", "llm": name, "api_key_ref": api_key_ref})
        dropped = sorted(set(model_configs) - {model})
        if dropped:
            _append(report, "dropped_metadata", {"table": "llm_backends", "id": name, "field": "model_configs", "models": dropped})
        protocol = _provider_protocol(_string(data.get("provider_identity")))
        config = {
            "endpoint": endpoint,
            "api_key": "",
            "options": {key: value for key, value in provider_options.items() if key not in {"base_url", "endpoint", "api_key"}},
        }
        record = ProviderRecord(id=name, name=name, protocol=protocol, config=config)
        await db.execute(
            """
            insert or replace into llm_providers (id, name, protocol, config, last_error, updated_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (name, record.name, record.protocol, msgspec.json.encode(record.config), None, _timestamp(data.get("updated_at"))),
        )
        await db.execute(
            """
            insert or replace into model_cards (provider_id, selector, payload, updated_at)
            values (?, ?, ?, ?)
            """,
            (name, model, msgspec.json.encode(ModelCard(selector=model)), _timestamp(data.get("updated_at"))),
        )


async def _import_integrations(
    db: Database,
    old: aiosqlite.Connection,
    tables: set[str],
    old_config_info: dict[str, object],
    report: dict[str, object],
) -> None:
    for row in await _rows(old, "integrations", tables):
        data = dict(row)
        config = _json_obj(data.get("config"), {})
        enabled = bool(data.get("enabled", 1))
        last_error: dict[str, object] | None = None
        try:
            config = _decrypt_config(config, str(old_config_info.get("master_key") or ""))
        except Exception as exc:
            enabled = False
            last_error = {"type": type(exc).__name__, "message": "legacy encrypted secret could not be decrypted"}
            _append(report, "manual_actions", {"type": "integration_secret", "integration": data.get("id"), "reason": str(exc)})
        record = IntegrationRecord(id=_string(data.get("id") or data.get("name")), type=_string(data.get("name")), name=_string(data.get("name")), config=config)
        await db.execute(
            "insert or replace into app_integrations (type, payload, enabled, last_error, updated_at) values (?, ?, ?, ?, ?)",
            (record.type, msgspec.json.encode(record), int(enabled), msgspec.json.encode(last_error) if last_error else None, _timestamp(data.get("updated_at"))),
        )


async def _import_actors(
    db: Database,
    old: aiosqlite.Connection,
    tables: set[str],
    capabilities: dict[str, dict[str, object]],
    data_dir: Path,
    old_data_dir: Path,
    report: dict[str, object],
) -> None:
    for row in await _rows(old, "actors", tables):
        data = dict(row)
        actor_id = _string(data.get("id") or data.get("name"))
        capability = capabilities.get(_string(data.get("capability_set_id")), {})
        workspace = _workspace(actor_id, capability, data_dir, old_data_dir, report)
        record = ActorRecord(
            id=actor_id,
            name=_string(data.get("name") or actor_id),
            description=_description(data, capability),
            workspace=workspace,
            persona=_string(data.get("persona_prompt")),
            provider=_string(data.get("llm_backend_id")),
            model=ModelCard(selector=_string(data.get("model") or "default")),
        )
        await db.execute(
            "insert or replace into app_actors (id, payload, enabled, status, last_error, updated_at) values (?, ?, ?, ?, ?, ?)",
            (actor_id, msgspec.json.encode(record), int(bool(data.get("enabled", 1))), "idle", None, _timestamp(data.get("updated_at"))),
        )
        _record_actor_dropped_metadata(data, capability, report)


async def _import_routes(db: Database, old: aiosqlite.Connection, tables: set[str], report: dict[str, object]) -> None:
    del report
    for row in await _rows(old, "actor_ingress_rules", tables):
        data = dict(row)
        pattern = _string(data.get("source_path_pattern") or data.get("source_id_pattern") or "**")
        route = RouteRecord(
            id=_string(data.get("id") or pattern),
            integration_type=_integration_type_from_source(_string(data.get("source_id_pattern"))),
            pattern=pattern,
            actor_id=_string(data.get("actor_id")),
            enabled=bool(data.get("enabled", 1)),
        )
        await db.execute(
            """
            insert or replace into app_routes (id, integration_type, pattern, actor_id, enabled, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (route.id, route.integration_type, route.pattern, route.actor_id, int(route.enabled), _timestamp(data.get("created_at")), _timestamp(data.get("updated_at"))),
        )


async def _import_conversations(db: Database, old: aiosqlite.Connection, tables: set[str], report: dict[str, object]) -> None:
    for row in await _rows(old, "conversations", tables):
        data = dict(row)
        conversation_id = _string(data.get("conversation_id"))
        await db.execute(
            """
            insert or replace into app_conversations (id, actor_id, status, created_at, last_active_at, last_error)
            values (?, ?, 'idle', ?, ?, null)
            """,
            (conversation_id, _string(data.get("actor_id")), _timestamp(data.get("created_at")), _timestamp(data.get("updated_at"))),
        )
        for field in ("metadata", "reply_address", "title"):
            value = data.get(field)
            if value not in (None, "", "{}", {}):
                _append(report, "dropped_metadata", {"table": "conversations", "id": conversation_id, "field": field})


async def _import_history(
    db: Database,
    old: aiosqlite.Connection,
    tables: set[str],
    unconverted_path: Path,
    report: dict[str, object],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = await _rows(old, "conversation_history_items", tables, order_by="conversation_id, id")
    for row in rows:
        data = dict(row)
        conversation_id = _string(data.get("conversation_id"))
        seq = counts.get(conversation_id, 0)
        converted = _convert_history_item(data)
        if converted is None:
            _append_jsonl(unconverted_path, data)
            _append(report, "manual_actions", {"type": "history_item", "conversation_id": conversation_id, "id": data.get("id"), "reason": "unrecognized legacy history item"})
            continue
        kind, payload = converted
        await db.execute(
            "insert or ignore into history (conversation_id, seq, kind, payload, created_at) values (?, ?, ?, ?, ?)",
            (conversation_id, seq, kind, payload, _timestamp(data.get("created_at"))),
        )
        counts[conversation_id] = seq + 1
    return counts


async def _import_messages(
    db: Database,
    old: aiosqlite.Connection,
    tables: set[str],
    history_counts: dict[str, int],
    report: dict[str, object],
) -> None:
    rows = await _rows(old, "conversation_messages", tables, order_by="conversation_id, timestamp, id")
    for row in rows:
        data = dict(row)
        conversation_id = _string(data.get("conversation_id"))
        if history_counts.get(conversation_id, 0) > 0:
            continue
        seq = history_counts.get(conversation_id, 0)
        converted = _message_to_history(data)
        if converted is None:
            _append(report, "manual_actions", {"type": "conversation_message", "conversation_id": conversation_id, "id": data.get("id"), "reason": "unsupported role"})
            continue
        kind, payload = converted
        await db.execute(
            "insert or ignore into history (conversation_id, seq, kind, payload, created_at) values (?, ?, ?, ?, ?)",
            (conversation_id, seq, kind, payload, _timestamp(data.get("created_at") or _from_unix(data.get("timestamp")))),
        )
        history_counts[conversation_id] = seq + 1


def _convert_history_item(data: dict[str, object]) -> tuple[str, bytes] | None:
    item_kind = _string(data.get("item_kind"))
    raw = _json_any(data.get("item_json"), None)
    if item_kind == "tools":
        specs = raw if isinstance(raw, list) else raw.get("tools", []) if isinstance(raw, dict) else []
        return "tool_specs", msgspec.json.encode(HistoryToolSpecs(specs=[x for x in specs if isinstance(x, dict)]))
    if item_kind != "message":
        return None
    if not isinstance(raw, dict):
        return None
    return _legacy_message_dict_to_history(raw)


def _legacy_message_dict_to_history(raw: dict[str, object]) -> tuple[str, bytes] | None:
    role = _string(raw.get("role"))
    content = raw.get("content", raw.get("raw_content", ""))
    name = _string(raw.get("name") or role or "legacy")
    if role in {"user", "developer", "system"}:
        input_role = "developer" if role == "system" else role
        return "input", msgspec.json.encode(InputMessage(role=input_role, name=name, content=_content_items(content)))
    if role == "assistant":
        return "gen_text", msgspec.json.encode(GenText(text=_content_text(content)))
    if role == "tool":
        return "tool_result", msgspec.json.encode(ToolResult(tool_call_id=_string(raw.get("tool_call_id") or raw.get("id") or "legacy"), content=_content_items(content)))
    return None


def _message_to_history(data: dict[str, object]) -> tuple[str, bytes] | None:
    role = _string(data.get("role"))
    content = _string(data.get("raw_content"))
    if role in {"user", "developer", "system"}:
        return "input", msgspec.json.encode(InputMessage(role="developer" if role == "system" else role, name=role or "legacy", content=_content_items(content)))
    if role == "assistant":
        return "gen_text", msgspec.json.encode(GenText(text=content))
    return None


async def _rows(old: aiosqlite.Connection, table: str, tables: set[str], *, order_by: str = "rowid") -> list[aiosqlite.Row]:
    if table not in tables:
        return []
    cursor = await old.execute(f'select * from "{table}" order by {order_by}')
    return await cursor.fetchall()


async def _table_names(connection: aiosqlite.Connection) -> set[str]:
    cursor = await connection.execute("select name from sqlite_master where type = 'table'")
    return {str(row[0]) for row in await cursor.fetchall()}


async def _already_imported(db: Database, source: Path) -> bool:
    cursor = await db.execute("select name from sqlite_master where type = 'table' and name = 'legacy_imports'")
    if await cursor.fetchone() is None:
        return False
    cursor = await db.execute("select 1 from legacy_imports where source_path = ?", (str(source),))
    return await cursor.fetchone() is not None


async def _has_application_rows(db: Database) -> bool:
    for table in ("llm_providers", "model_cards", "app_integrations", "app_actors", "app_routes", "app_conversations", "history"):
        cursor = await db.execute(f'select count(*) from "{table}"')
        row = await cursor.fetchone()
        if row is not None and int(row[0]) > 0:
            return True
    return False


def _backup_legacy_db(source: Path) -> Path:
    backup = source.with_name(f"{source.name}.backup.{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(source, backup)
    return backup


def _old_data_dir(info: dict[str, object], legacy_db: Path) -> Path:
    raw = str(info.get("data_dir") or "")
    if raw:
        return Path(raw).expanduser()
    if legacy_db.parent.name == "yuubot":
        return legacy_db.parent.parent
    return legacy_db.parent


def _workspace(actor_id: str, capability: dict[str, object], data_dir: Path, old_data_dir: Path, report: dict[str, object]) -> str:
    raw = _string(capability.get("workspace_path"))
    if raw:
        path = Path(raw).expanduser()
        if path.is_absolute():
            if not path.exists():
                _append(report, "manual_actions", {"type": "workspace", "actor": actor_id, "path": str(path), "reason": "absolute path missing"})
            return str(path)
        source = old_data_dir / raw
    else:
        source = old_data_dir / "workspace" / "actors" / actor_id
    target = data_dir / "workspace" / actor_id
    if source.exists() and not target.exists():
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return str(target)


def _description(actor: dict[str, object], capability: dict[str, object]) -> str:
    config = _json_obj(actor.get("config"), {})
    return _string(capability.get("description") or config.get("description"))


def _record_actor_dropped_metadata(actor: dict[str, object], capability: dict[str, object], report: dict[str, object]) -> None:
    actor_id = _string(actor.get("id"))
    for field in ("generation_override", "per_run_budget", "skill_scope"):
        value = actor.get(field)
        if value not in (None, "", "{}", {}):
            _append(report, "dropped_metadata", {"table": "actors", "id": actor_id, "field": field})
    for field in ("loop_policy", "integration_ids", "tools"):
        value = capability.get(field)
        if value not in (None, "", "{}", {}, "[]", []):
            _append(report, "dropped_metadata", {"table": "capability_sets", "id": capability.get("id"), "field": field})


def _decrypt_config(config: dict[str, object], master_key: str) -> dict[str, object]:
    if not _contains_encrypted_marker(config):
        return config
    if not master_key:
        raise LegacyImportError("old config secrets.master_key is required to decrypt integration secrets")
    codec = _SecretCodec(master_key)
    return _decrypt_value(config, codec)


def _decrypt_value(value: object, codec: "_SecretCodec") -> Any:
    if isinstance(value, dict):
        if value.get("$enc") == "v1" and isinstance(value.get("ct"), str):
            return codec.decrypt(str(value["ct"]))
        return {str(key): _decrypt_value(item, codec) for key, item in value.items()}
    if isinstance(value, list):
        return [_decrypt_value(item, codec) for item in value]
    return value


class _SecretCodec:
    def __init__(self, master_key: str) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aead = AESGCM(_decode_master_key(master_key))

    def decrypt(self, ciphertext: str) -> str:
        payload = base64.b64decode(ciphertext.encode(), validate=True)
        if len(payload) < 13:
            raise ValueError("secret ciphertext is too short")
        return self._aead.decrypt(payload[:12], payload[12:], None).decode()


def _decode_master_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode(), validate=True)
    except binascii.Error as exc:
        raise ValueError("secrets.master_key must be base64") from exc
    if len(decoded) != 32:
        raise ValueError("secrets.master_key must decode to 32 bytes")
    return decoded


def _contains_encrypted_marker(value: object) -> bool:
    if isinstance(value, dict):
        return value.get("$enc") == "v1" or any(_contains_encrypted_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_encrypted_marker(item) for item in value)
    return False


def _contains_secret(value: object) -> bool:
    if isinstance(value, dict):
        return any("key" in str(key).lower() or _contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _provider_protocol(value: str) -> str:
    normalized = (value or "openai_compatible").replace("_", "-")
    if normalized in {"openai", "deepseek"}:
        return "openai-compatible"
    return normalized


def _provider(value: str) -> str:
    return value or "openai_compatible"


def _integration_type_from_source(value: str) -> str:
    if ":" not in value or value.startswith("*"):
        return ""
    return value.split(":", 1)[0]


def _legacy_secret_ref(name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name.upper()).strip("_")
    return f"YUU_LEGACY_{safe or 'LLM'}_API_KEY"


def _content_items(value: object) -> list[ContentItem]:
    text = _content_text(value)
    return [ContentItem(kind="text", text=text)] if text else []


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return text
    return ""


def _json_obj(value: object, default: dict[str, object]) -> dict[str, object]:
    decoded = _json_any(value, default)
    return decoded if isinstance(decoded, dict) else dict(default)


def _json_any(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode()
    if isinstance(value, str):
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _string(value: object) -> str:
    return value if isinstance(value, str) else "" if value is None else str(value)


def _timestamp(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    return _now()


def _from_unix(value: object) -> str:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    return _now()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append(report: dict[str, object], key: str, value: object) -> None:
    items = report.setdefault(key, [])
    if isinstance(items, list):
        items.append(value)


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, default=str, ensure_ascii=False) + "\n")
