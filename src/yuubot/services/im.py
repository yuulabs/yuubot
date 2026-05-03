"""IM domain service for QQ message IO and message-store queries."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import attrs
import httpx
import msgspec
from tortoise import connections

from yuubot.config import Config
from yuubot.core.models import (
    Context,
    ForwardRecord,
    ImageSegment,
    Message,
    MessageRecord,
    ReactSegment,
    Segment,
    TextSegment,
    segments_from_json,
)
from yuubot.core.onebot import build_send_msg, parse_segments
from yuubot.rendering import MessageList
from yuubot.services.base import InvalidScope, AccessDenied, YuubotServiceError


EMOJI_ALIASES: dict[str, str] = {
    "thumbsup": "76",
    "heart": "66",
    "laugh": "178",
    "cry": "5",
    "cool": "16",
    "doge": "179",
    "cute": "21",
    "ok": "124",
    "rose": "63",
    "fire": "128293",
    "clap": "99",
    "hug": "49",
    "think": "32",
    "salute": "282",
    "respect": "318",
    "celebrate": "320",
    "angry": "326",
    "question": "10068",
    "press_button": "424",
    "button": "424",
}


def _is_master(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("bot_kind", "")).lower() == "master"


def _current_ctx(payload: Mapping[str, Any]) -> int:
    return _int(payload.get("ctx_id"))


def _int(value: object) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return 0
    return 0


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _normalize_media_files(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.startswith(("/", "file://")) else []
        if isinstance(decoded, list):
            return [str(item) for item in decoded if item]
    return []


def _message_row_to_dict(row: Sequence[Any]) -> dict[str, Any]:
    from yuubot.rendering import render_message_xml

    raw_message = str(row[8] or "[]")
    timestamp_str = _iso(row[2])
    try:
        parsed_segments = segments_from_json(raw_message)
    except Exception:
        parsed_segments = []
    rendered = render_message_xml(
        uid=row[3],
        name=row[4] or "",
        display_name=row[5] or "",
        time=timestamp_str,
        segments=parsed_segments,
        message_id=row[1],
    )
    return {
        "db_id": _int(row[0]),
        "message_id": row[1],
        "timestamp": timestamp_str,
        "user_id": row[3],
        "nickname": row[4] or "",
        "display_name": row[5] or "",
        "ctx_id": row[6],
        "content": row[7] or "",
        "raw_message": raw_message,
        "segments": msgspec.to_builtins(parsed_segments),
        "media_files": _normalize_media_files(row[9]),
        "rendered": rendered,
    }


def _rows_to_messages(rows: Sequence[Sequence[Any]]) -> MessageList:
    return MessageList(_message_row_to_dict(row) for row in rows)


def _content_to_segments(items: list[Any]) -> list[Segment]:
    """Convert a yuullm.Content list to internal Segment list.

    Each item is a dict with ``"type"``:
    - ``{"type": "text", "text": "..."}`` → ``TextSegment``
    - ``{"type": "image_url", "image_url": {"url": "..."}}`` → ``ImageSegment``
      (``file://`` URLs go into the ``file`` field; others into ``url``)
    """
    result: list[Segment] = []
    for item in items:
        t = item.get("type", "")
        if t == "text":
            result.append(TextSegment(text=str(item.get("text", ""))))
        elif t == "image_url":
            url = str((item.get("image_url") or {}).get("url", ""))
            if url.startswith("file://"):
                result.append(ImageSegment(file=url))
            else:
                result.append(ImageSegment(url=url))
        else:
            raise YuubotServiceError(f"unsupported content item type: {t!r}")
    if not result:
        raise YuubotServiceError("content list is empty")
    return result


def _segments_from_payload(payload: Mapping[str, Any]) -> Message:
    # Internal callers may pass OneBot-style segment dicts directly.
    segments = payload.get("segments")
    if segments is not None:
        if not isinstance(segments, list):
            raise YuubotServiceError("segments must be a list")
        return parse_segments(segments)
    # Agent callers pass yuullm.Content (str or list of content blocks).
    content = payload.get("content")
    if content is not None:
        if isinstance(content, str):
            if not content.strip():
                raise YuubotServiceError("message text is empty")
            return [TextSegment(text=content)]
        if not isinstance(content, list):
            raise YuubotServiceError("content must be a str or list")
        return _content_to_segments(content)
    # Legacy plain-text path.
    text = str(payload.get("text", "") or "")
    if not text.strip():
        raise YuubotServiceError("message text is empty")
    return [TextSegment(text=text)]


async def _ctx_info(ctx_id: int) -> tuple[str, int] | None:
    ctx = await Context.get_or_none(id=ctx_id)
    if ctx is None:
        return None
    return str(ctx.type), int(ctx.target_id)


async def _resolve_target(payload: Mapping[str, Any]) -> tuple[int, str, int]:
    requested_ctx = _int(payload.get("target_ctx_id") or payload.get("ctx_id"))
    current_ctx = _current_ctx(payload)
    explicit_user = _int(payload.get("user_id") or payload.get("target_user_id"))
    explicit_group = _int(payload.get("group_id") or payload.get("target_group_id"))

    if requested_ctx and requested_ctx != current_ctx and not _is_master(payload):
        raise InvalidScope(f"ctx {requested_ctx} is outside current group scope")
    if (explicit_user or explicit_group) and not _is_master(payload):
        raise AccessDenied("only master may send to an explicit QQ target")

    if explicit_group:
        return 0, "group", explicit_group
    if explicit_user:
        return 0, "private", explicit_user

    target_ctx = requested_ctx or current_ctx
    if target_ctx:
        if target_ctx == current_ctx:
            chat_type = str(payload.get("chat_type", "") or "")
            if chat_type == "group" and _int(payload.get("group_id")):
                return target_ctx, "group", _int(payload.get("group_id"))
            if chat_type == "private" and _int(payload.get("user_id")):
                return target_ctx, "private", _int(payload.get("user_id"))
        info = await _ctx_info(target_ctx)
        if info is not None:
            return target_ctx, info[0], info[1]

    raise InvalidScope("target context is unavailable")


@attrs.define
class ImService:
    config: Config | None = None

    async def send_message(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        ctx_id, chat_type, target_id = await _resolve_target(payload)
        segments = _segments_from_payload(payload)
        body = build_send_msg(chat_type, target_id, segments)
        recorder_api = self._recorder_api(payload)
        endpoint = "/send_msg_guaranteed" if bool(payload.get("guaranteed", False)) else "/send_msg"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{recorder_api}{endpoint}",
                json=body,
                headers={"X-Bot-Mode": "1"},
            )
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text}
        if response.status_code >= 400:
            raise YuubotServiceError(f"send failed ({response.status_code}): {data}")
        return {
            "status": "sent",
            "ctx_id": ctx_id,
            "message_type": chat_type,
            "target_id": target_id,
            "segments": msgspec.to_builtins(segments),
            "recorder": data,
        }

    async def send_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        ctx_id, chat_type, target_id = await _resolve_target(payload)

        path_str = str(payload.get("path", "") or "").strip()
        if not path_str:
            raise YuubotServiceError("path is required")

        from yuubot.core.media_paths import input_to_host
        local_path = Path(input_to_host(path_str)).expanduser()
        if not local_path.is_absolute():
            ws = str(payload.get("workspace_root", "") or "")
            local_path = (Path(ws) / local_path).resolve() if ws else local_path.resolve()
        if not local_path.is_file():
            raise YuubotServiceError(f"file not found: {local_path}")

        name = str(payload.get("name", "") or local_path.name)

        if self.config is None:
            raise YuubotServiceError("config is not available")
        self_url = self.config.daemon.self_url.rstrip("/")
        file_url = f"{self_url}/internal/serve?path={urllib.parse.quote(str(local_path))}"

        napcat_http = self.config.recorder.napcat_http.rstrip("/")
        if chat_type == "group":
            endpoint = "/upload_group_file"
            body: dict = {"group_id": target_id, "file": file_url, "name": name}
        else:
            endpoint = "/upload_private_file"
            body = {"user_id": target_id, "file": file_url, "name": name}

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{napcat_http}{endpoint}", json=body)
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text}
        if response.status_code >= 400:
            raise YuubotServiceError(f"file upload failed ({response.status_code}): {data}")

        return {
            "status": "sent",
            "ctx_id": ctx_id,
            "chat_type": chat_type,
            "target_id": target_id,
            "file": str(local_path),
            "name": name,
            "napcat": data,
        }

    async def recent_messages(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        ctx_id = self._scoped_ctx(payload)
        limit = max(1, min(_int(payload.get("limit")) or 30, 200))
        after_row_id = _int(payload.get("after_row_id"))
        upto_row_id = _int(payload.get("upto_row_id")) or None
        filter_user_id = _int(payload.get("filter_user_id")) or None
        conditions = ["ctx_id = ?", "id > ?"]
        params: list[object] = [ctx_id, after_row_id]
        if upto_row_id is not None:
            conditions.append("id <= ?")
            params.append(upto_row_id)
        if filter_user_id is not None:
            conditions.append("user_id = ?")
            params.append(filter_user_id)
        sql = f"""
            SELECT id, message_id, timestamp, user_id, nickname, display_name, ctx_id, content, raw_message, media_files
            FROM messages
            WHERE {" AND ".join(conditions)}
            ORDER BY id DESC
            LIMIT ?
        """
        params.append(limit)
        conn = connections.get("default")
        _, rows = await conn.execute_query(sql, params)
        ordered = list(rows)
        ordered.reverse()
        return _rows_to_messages(ordered)

    async def search_messages(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = str(payload.get("query", "") or "").strip()
        if not query:
            return []
        limit = max(1, min(_int(payload.get("limit")) or 20, 100))
        days = max(1, min(_int(payload.get("days")) or 180, 3650))
        ctx_id = payload.get("target_ctx_id") if "target_ctx_id" in payload else payload.get("ctx_id")
        scoped_ctx = self._scoped_ctx({**payload, "ctx_id": ctx_id}) if ctx_id else None
        if scoped_ctx is None and not _is_master(payload):
            scoped_ctx = _current_ctx(payload)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        words = [word for word in query.split() if word]
        conn = connections.get("default")
        if not words:
            return []
        fts_query = " OR ".join(_fts_phrase(word) for word in words)
        fts_where = [
            "id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)",
            "timestamp >= ?",
        ]
        fts_params: list[object] = [fts_query, since]
        if scoped_ctx:
            fts_where.append("ctx_id = ?")
            fts_params.append(scoped_ctx)
        if _int(payload.get("filter_user_id")):
            fts_where.append("user_id = ?")
            fts_params.append(_int(payload.get("filter_user_id")))
        if _int(payload.get("before_id")):
            fts_where.append("id < ?")
            fts_params.append(_int(payload.get("before_id")))
        sql = f"""
            SELECT id, message_id, timestamp, user_id, nickname, display_name, ctx_id, content, raw_message, media_files
            FROM messages
            WHERE {" AND ".join(fts_where)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        _, rows = await conn.execute_query(sql, [*fts_params, limit])
        return _rows_to_messages(rows)

    async def read_forward(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        forward_id = str(payload.get("forward_id", "") or "")
        if not forward_id:
            raise YuubotServiceError("forward_id is required")
        record = await ForwardRecord.filter(forward_id=forward_id).first()
        if record is None:
            return []
        nodes = json.loads(record.raw_nodes or "[]")
        return [dict(node) for node in nodes if isinstance(node, dict)]

    async def list_contacts(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not _is_master(payload):
            raise AccessDenied("contacts are master-only")
        recorder_api = self._recorder_api(payload)
        async with httpx.AsyncClient(timeout=10) as client:
            groups = await self._safe_get(client, f"{recorder_api}/get_group_list")
            friends = await self._safe_get(client, f"{recorder_api}/get_friend_list")
            contexts = await self._safe_get(client, f"{recorder_api}/ctx")
        return {
            "groups": groups.get("data", groups) if isinstance(groups, dict) else groups,
            "friends": friends.get("data", friends) if isinstance(friends, dict) else friends,
            "contexts": contexts,
        }

    async def react_message(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        message_id = _int(payload.get("message_id"))
        if not message_id:
            raise YuubotServiceError("message_id is required")
        emoji = str(payload.get("emoji_id", payload.get("emoji", "")) or "")
        emoji_id = EMOJI_ALIASES.get(emoji.lower(), emoji)
        if not emoji_id:
            raise YuubotServiceError("emoji_id is required")
        record = await MessageRecord.filter(message_id=message_id).order_by("-id").first()
        if record is None:
            raise YuubotServiceError(f"message {message_id} was not found")
        record_ctx_id = getattr(record, "ctx_id")
        assert isinstance(record_ctx_id, int)
        if record_ctx_id != _current_ctx(payload) and not _is_master(payload):
            raise InvalidScope(f"message {message_id} is outside current group scope")
        ctx = await Context.filter(id=record_ctx_id).first()
        if ctx is None:
            raise YuubotServiceError(f"context for message {message_id} was not found")
        segments: Message = [ReactSegment(message_id=str(message_id), emoji_id=str(emoji_id))]
        body = build_send_msg(ctx.type, int(ctx.target_id), segments)
        recorder_api = self._recorder_api(payload)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{recorder_api}/send_msg", json=body, headers={"X-Bot-Mode": "1"})
        if response.status_code >= 400:
            raise YuubotServiceError(f"reaction failed ({response.status_code}): {response.text}")
        return {"status": "reacted", "message_id": message_id, "emoji_id": emoji_id}

    def _scoped_ctx(self, payload: Mapping[str, Any]) -> int:
        requested = _int(payload.get("target_ctx_id") or payload.get("ctx_id"))
        current = _current_ctx(payload)
        if not requested:
            requested = current
        if requested != current and not _is_master(payload):
            raise InvalidScope(f"ctx {requested} is outside current group scope")
        if not requested:
            raise InvalidScope("ctx_id is required")
        return requested

    def _recorder_api(self, payload: Mapping[str, Any]) -> str:
        value = str(payload.get("recorder_base_url", "") or "")
        if value:
            return value.rstrip("/")
        if self.config is not None:
            return self.config.daemon.recorder_api.rstrip("/")
        raise YuubotServiceError("recorder API is not configured")

    async def _safe_get(self, client: httpx.AsyncClient, url: str) -> Any:
        response = await client.get(url)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json()
