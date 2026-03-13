"""Forward-message fetching, normalization, and summary building."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

import httpx

from yuubot.core.models import ForwardRecord, ForwardSegment, Message, TextSegment, segments_to_json, segments_to_plain
from yuubot.core.onebot import parse_segments


def _coerce_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat()


def build_forward_summary(nodes: list[dict[str, Any]]) -> str:
    """Build a short summary from the first three nodes, capped at 100 chars."""
    snippets: list[str] = []
    for node in nodes[:3]:
        text = str(node.get("content", "")).strip()
        if text:
            snippets.append(text)
    summary = " / ".join(snippets)
    return summary[:100]


def _extract_forward_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    payload = data.get("data", data)
    messages = payload.get("messages")
    if isinstance(messages, list):
        return messages
    if isinstance(payload, list):
        return payload
    return []


def _render_forward_log_lines(
    forward_id: str,
    nodes: list[dict[str, Any]],
    *,
    max_depth: int = 3,
    depth: int = 1,
) -> list[str]:
    lines: list[str] = []
    prefix = "  " * max(depth - 1, 0)
    for node in nodes:
        name = node.get("nickname") or node.get("display_name") or node.get("user_id") or "?"
        content = node.get("content", "")
        lines.append(f"{prefix}[forward:{forward_id}] {name}: {content}")
        if depth >= max_depth:
            continue
        children = node.get("children", [])
        if children:
            lines.extend(_render_forward_log_lines(forward_id, children, max_depth=max_depth, depth=depth + 1))
    return lines


class ForwardResolver:
    def __init__(self, napcat_http: str) -> None:
        self._client = httpx.AsyncClient(base_url=napcat_http, timeout=30)

    async def close(self) -> None:
        await self._client.aclose()

    async def resolve(
        self,
        forward_id: str,
        *,
        source_message_id: int | None,
        source_ctx_id: int | None,
        max_depth: int = 3,
        _depth: int = 1,
    ) -> dict[str, Any] | None:
        record = await ForwardRecord.filter(forward_id=forward_id).first()
        if record is not None:
            nodes = json.loads(record.raw_nodes)
            return {"summary": record.summary, "nodes": nodes, "log_nodes": nodes}

        response = await self._client.get("/get_forward_msg", params={"id": forward_id})
        if response.status_code != 200:
            return None

        payload = response.json()
        raw_nodes = _extract_forward_payload(payload)
        nodes: list[dict[str, Any]] = []
        log_nodes: list[dict[str, Any]] = []

        for raw_node in raw_nodes:
            data = raw_node.get("data", raw_node)
            content_value = data.get("content") or data.get("message") or []
            if isinstance(content_value, list):
                segments: Message = parse_segments(content_value)
            else:
                segments = cast(Message, [TextSegment(text=str(content_value))])

            child_logs: list[dict[str, Any]] = []
            if _depth < max_depth:
                for seg in segments:
                    if not isinstance(seg, ForwardSegment):
                        continue
                    child = await self.resolve(
                        seg.id,
                        source_message_id=source_message_id,
                        source_ctx_id=source_ctx_id,
                        max_depth=max_depth,
                        _depth=_depth + 1,
                    )
                    if child and child.get("summary") and not seg.summary:
                        seg.summary = str(child["summary"])
                    if child:
                        child_logs.extend(child["log_nodes"])

            plain = segments_to_plain(segments)
            node = {
                "message_id": int(data.get("message_id") or 0),
                "user_id": int(data.get("uin") or data.get("user_id") or 0),
                "nickname": str(data.get("name") or data.get("nickname") or ""),
                "display_name": str(data.get("display_name") or ""),
                "timestamp": _coerce_timestamp(data.get("time")),
                "content": plain,
                "raw_message": segments_to_json(segments),
                "media_files": [],
            }
            nodes.append(node)
            log_nodes.append({**node, "children": child_logs})

        summary = build_forward_summary(nodes)
        await ForwardRecord.update_or_create(
            defaults={
                "summary": summary,
                "raw_nodes": json.dumps(nodes, ensure_ascii=False),
                "source_message_id": source_message_id,
                "source_ctx_id": source_ctx_id,
            },
            forward_id=forward_id,
        )
        return {"summary": summary, "nodes": nodes, "log_nodes": log_nodes}
