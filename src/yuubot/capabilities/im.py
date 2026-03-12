"""IM capability — send, search, browse, list messages."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import httpx

from yuubot.capabilities import capability, get_context, text_block, ContentBlock
from yuubot.config import load_config
from yuubot.skills.im.query import search_messages, browse_messages
from yuubot.skills.im.formatter import format_messages_to_xml


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


def _enforce_bot_ctx(ctx_id: int | None) -> int | None:
    actx = get_context()
    if actx.ctx_id is not None:
        if ctx_id is not None and ctx_id != actx.ctx_id:
            return actx.ctx_id
        return actx.ctx_id
    return ctx_id


def _normalize_segment(seg: dict) -> dict:
    if "data" in seg:
        return seg
    seg_type = seg.get("type", "text")
    data = {k: v for k, v in seg.items() if k != "type"}
    if seg_type == "image":
        for key in ("file", "url"):
            val = data.get(key, "")
            if val and val.startswith("/") and not val.startswith("//"):
                data[key] = f"file://{val}"
    return {"type": seg_type, "data": data}


@capability("im")
class ImCapability:

    async def send(
        self,
        *,
        ctx: int | None = None,
        uid: int | None = None,
        gid: int | None = None,
        delay: float = 0,
        data: list[dict] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        if delay > 0:
            import asyncio
            await asyncio.sleep(delay)

        cfg = _get_config()
        api = cfg.daemon.recorder_api

        if ctx is not None:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{api}/ctx/{ctx}")
                if r.status_code != 200:
                    return [text_block(f"错误: ctx {ctx} 不存在")]
                info = r.json()
                msg_type = info["type"]
                target_id = info["target_id"]
        elif uid is not None:
            msg_type = "private"
            target_id = uid
        elif gid is not None:
            msg_type = "group"
            target_id = gid
        else:
            return [text_block("错误: 必须指定 --ctx, --uid 或 --gid")]

        if not data:
            return [text_block("错误: 消息内容为空 (需要 -- 后跟 JSON 数组)")]

        segments = [_normalize_segment(s) for s in data]

        if all(
            s.get("type") == "text" and not s.get("data", {}).get("text", "").strip()
            for s in segments
        ):
            return [text_block("错误: 消息内容为空")]

        for seg in segments:
            if seg.get("type") != "image":
                continue
            seg_data = seg.get("data", {})
            for key in ("file", "url"):
                val = seg_data.get(key, "")
                if val and val.startswith("file://"):
                    fpath = val[len("file://"):]
                    if not os.path.isfile(fpath):
                        return [text_block(f"错误: 图片文件不存在: {fpath}")]

        body: dict[str, Any] = {"message_type": msg_type, "message": segments}
        if msg_type == "group":
            body["group_id"] = target_id
        else:
            body["user_id"] = target_id

        headers: dict[str, str] = {"X-Bot-Mode": "1"}

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{api}/send_msg", json=body, headers=headers)
            if r.status_code == 200:
                resp = r.json()
                if "remaining" in resp:
                    return [text_block(f"消息已发送 (剩余额度: {resp['remaining']}/5)")]
                return [text_block("消息已发送")]
            elif r.status_code == 429:
                return [text_block("发送失败: 群聊限流，每分钟最多5条")]
            else:
                return [text_block(f"发送失败: {r.text}")]

    async def search(
        self,
        *,
        _positional: list[str] | None = None,
        ctx: int | None = None,
        limit: int = 20,
        days: int = 7,
        **_kw,
    ) -> list[ContentBlock]:
        keywords = " ".join(_positional) if _positional else ""
        if not keywords:
            return [text_block("错误: 请提供搜索关键词")]

        ctx_id = _enforce_bot_ctx(ctx)
        cfg = _get_config()
        results = await search_messages(keywords, ctx_id, limit, days)
        if not results:
            return [text_block("未找到消息")]
        xml_output = await format_messages_to_xml(results)
        return [text_block(xml_output)]

    async def browse(
        self,
        *,
        msg: int | None = None,
        ctx: int | None = None,
        before: int = 10,
        after: int = 10,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        **_kw,
    ) -> list[ContentBlock]:
        ctx_id = _enforce_bot_ctx(ctx)
        cfg = _get_config()
        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        results = await browse_messages(
            msg_id=msg,
            ctx_id=ctx_id,
            before=before,
            after=after,
            since=since_dt,
            until=until_dt,
            limit=limit,
        )
        if not results:
            return [text_block("未找到消息")]
        xml_output = await format_messages_to_xml(results)
        return [text_block(xml_output)]

    async def list(
        self,
        *,
        _positional: list[str] | None = None,
        gid: int | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        target = (_positional[0] if _positional else "").lower()
        if target not in ("friends", "groups", "members", "contexts"):
            return [text_block("错误: 请指定 friends, groups, members 或 contexts")]

        cfg = _get_config()
        api = cfg.daemon.recorder_api

        async with httpx.AsyncClient(timeout=10) as client:
            if target == "friends":
                r = await client.get(f"{api}/get_friend_list")
                data = r.json().get("data", r.json())
                if isinstance(data, list):
                    lines = [f"{f.get('user_id', '?')} — {f.get('nickname', '?')}" for f in data[:50]]
                    return [text_block("\n".join(lines))]
                return [text_block(json.dumps(data, ensure_ascii=False, indent=2))]

            elif target == "groups":
                r = await client.get(f"{api}/get_group_list")
                data = r.json().get("data", r.json())
                if isinstance(data, list):
                    lines = [f"{g.get('group_id', '?')} — {g.get('group_name', '?')}" for g in data[:50]]
                    return [text_block("\n".join(lines))]
                return [text_block(json.dumps(data, ensure_ascii=False, indent=2))]

            elif target == "members":
                if gid is None:
                    return [text_block("错误: 需要 --gid 参数")]
                r = await client.get(f"{api}/get_group_member_list", params={"group_id": gid})
                data = r.json().get("data", r.json())
                if isinstance(data, list):
                    lines = [f"{m.get('user_id', '?')} — {m.get('nickname', '?')}" for m in data[:100]]
                    return [text_block("\n".join(lines))]
                return [text_block(json.dumps(data, ensure_ascii=False, indent=2))]

            elif target == "contexts":
                r = await client.get(f"{api}/ctx")
                data = r.json()
                lines = [f"ctx {c['ctx_id']}: {c['type']} → {c['target_id']}" for c in data]
                return [text_block("\n".join(lines))]

        return [text_block("未知目标")]
