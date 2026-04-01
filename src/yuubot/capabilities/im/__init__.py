"""IM capability — send, search, browse, list messages."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any, TypedDict

import httpx

from yuubot.capabilities import capability, get_context, text_block, ContentBlock
from yuubot.config import load_config
from yuubot.core.media_paths import MediaPathContext, MediaPathError, input_to_host, to_file_uri
from .query import browse_messages, recent_messages, resolve_message_db_id, search_messages
from .formatter import format_forward_nodes_to_xml, format_messages_to_xml
from .political_filter import check_political_content
from yuubot.core.models import Context, ForwardRecord, MessageRecord

type ContentBlocks = list[ContentBlock]

class MessageEnvelope(TypedDict):
    msg: Sequence[dict[str, Any]]
    gap: float

_RATE_LIMIT_RETRY_INTERVAL = 15  # seconds between retries on 429


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


def _get_optional_context():
    try:
        return get_context()
    except RuntimeError:
        return None


def _media_path_ctx() -> MediaPathContext:
    actx = _get_optional_context()
    if actx is None:
        return MediaPathContext.from_env()
    return MediaPathContext.from_values(
        docker_host_mount=actx.docker_host_mount,
        host_home_dir=actx.docker_home_host_dir,
        container_home_dir=actx.docker_home_dir,
    )


def _enforce_bot_ctx(ctx_id: int | None) -> int | None:
    actx = get_context()
    if actx.ctx_id is not None:
        if ctx_id is not None and ctx_id != actx.ctx_id:
            return actx.ctx_id
        return actx.ctx_id
    return ctx_id


def _parse_qq_ids(qq: str | int | None) -> list[int] | None:
    if qq is None:
        return None
    if isinstance(qq, bool):
        raise ValueError
    if isinstance(qq, int):
        return [qq]
    if isinstance(qq, str):
        return [int(part.strip()) for part in qq.split(",") if part.strip()]
    raise ValueError


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


def _normalize_image_file_refs(segments: list[dict]) -> list[dict]:
    media_ctx = _media_path_ctx()
    normalized: list[dict] = []
    for seg in segments:
        if seg.get("type") != "image":
            normalized.append(seg)
            continue
        data = dict(seg.get("data", {}))
        for key in ("file", "url"):
            val = data.get(key, "")
            if isinstance(val, str) and val.startswith("file://"):
                host_path = input_to_host(val, ctx=media_ctx)
                data[key] = to_file_uri(host_path)
        normalized.append({"type": seg["type"], "data": data})
    return normalized


def _validate_segments(segments: list[dict]) -> str | None:
    """Return error string if segments are invalid, else None."""
    media_ctx = _media_path_ctx()
    if not segments:
        return "消息内容为空"
    if all(
        s.get("type") == "text" and not s.get("data", {}).get("text", "").strip()
        for s in segments
    ):
        return "消息内容为空"
    for seg in segments:
        if seg.get("type") != "image":
            continue
        for key in ("file", "url"):
            val = seg.get("data", {}).get(key, "")
            if val and val.startswith("file://"):
                try:
                    fpath = input_to_host(val, ctx=media_ctx)
                except MediaPathError as e:
                    return str(e)
                if not os.path.isfile(fpath):
                    return f"图片文件不存在: {fpath}"
    return None


async def _post_with_retry(client: httpx.AsyncClient, url: str, body: dict, headers: dict) -> httpx.Response:
    """POST to url, retrying indefinitely on 429 until quota refreshes."""
    while True:
        r = await client.post(url, json=body, headers=headers)
        if r.status_code != 429:
            return r
        await asyncio.sleep(_RATE_LIMIT_RETRY_INTERVAL)


@capability("im")
class ImCapability:

    async def send(
        self,
        *,
        ctx: int | None = None,
        uid: int | None = None,
        gid: int | None = None,
        delay: float = 0,
        data: Sequence[dict[str, Any]] | None = None,
        **_kw,
    ) -> ContentBlocks:
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

        # Normalize to envelope format: [{"msg": [...], "gap": 1.5}, ...]
        # Each element is either already an envelope (has "msg" key)
        # or a bare segment (has "type" key) — wrap bare segments.
        envelopes: list[MessageEnvelope] = []
        pending_segments: list[dict] = []
        for item in data:
            if isinstance(item, dict) and "msg" in item:
                if pending_segments:
                    envelopes.append({"msg": pending_segments, "gap": float(delay)})
                    pending_segments = []
                envelopes.append({
                    "msg": item["msg"],
                    "gap": float(item.get("gap", 0)),
                })
            else:
                pending_segments.append(item)
        if pending_segments:
            envelopes.append({"msg": pending_segments, "gap": float(delay)})

        headers: dict[str, str] = {"X-Bot-Mode": "1"}
        remaining: int | None = None

        async with httpx.AsyncClient(timeout=30) as client:
            for env in envelopes:
                gap = float(env["gap"])
                if gap > 0:
                    await asyncio.sleep(gap)

                try:
                    segments = [_normalize_segment(s) for s in env["msg"]]
                    segments = _normalize_image_file_refs(segments)
                except MediaPathError as e:
                    return [text_block(f"错误: {e}")]

                # Extract poke segments — handle them via /group_poke API
                poke_segs = [s for s in segments if s.get("type") == "poke"]
                normal_segs = [s for s in segments if s.get("type") != "poke"]

                for poke in poke_segs:
                    poke_qq = poke.get("data", {}).get("qq", "")
                    if poke_qq and msg_type == "group":
                        poke_body = {"group_id": target_id, "user_id": int(poke_qq)}
                        r = await client.post(f"{api}/group_poke", json=poke_body, timeout=10)
                        if r.status_code != 200:
                            return [text_block(f"戳一戳失败: {r.text}")]

                if not normal_segs:
                    continue  # All segments were poke, nothing left to send

                err = _validate_segments(normal_segs)
                if err:
                    return [text_block(f"错误: {err}")]

                # Political content filter
                all_text = "".join(
                    s.get("data", {}).get("text", "")
                    for s in normal_segs
                    if s.get("type") == "text"
                )
                hit = check_political_content(all_text)
                if hit:
                    return [text_block("安全策略: 消息包含敏感内容，已拦截")]

                body: dict[str, Any] = {"message_type": msg_type, "message": normal_segs}
                if msg_type == "group":
                    body["group_id"] = target_id
                else:
                    body["user_id"] = target_id

                r = await _post_with_retry(client, f"{api}/send_msg", body, headers)
                if r.status_code != 200:
                    return [text_block(f"发送失败: {r.text}")]
                resp = r.json()
                remaining = resp.get("remaining")

        n = len(envelopes)
        if remaining is not None:
            return [text_block(f"已发送 {n} 条消息 (剩余额度: {remaining}/5)")]
        return [text_block(f"已发送 {n} 条消息")]

    async def search(
        self,
        *,
        _positional: Sequence[str] | None = None,
        ctx: int | None = None,
        limit: int = 20,
        days: int = 7,
        **_kw,
    ) -> ContentBlocks:
        keywords = " ".join(_positional) if _positional else ""
        if not keywords:
            return [text_block("错误: 请提供搜索关键词")]

        ctx_id = _enforce_bot_ctx(ctx)
        results = await search_messages(keywords, ctx_id, limit, days)
        if not results:
            return [text_block("未找到消息")]
        actx = get_context()
        bot_cfg = getattr(actx.config, "bot", None)
        bot_qq = bot_cfg.qq if bot_cfg else None
        bot_name = actx.bot_name or None
        xml_output = await format_messages_to_xml(results, bot_qq=bot_qq, bot_name=bot_name)
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
        qq: str | int | None = None,
        name: str | None = None,
        **_kw,
    ) -> ContentBlocks:
        ctx_id = _enforce_bot_ctx(ctx)
        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        try:
            qq_ids = _parse_qq_ids(qq)
        except ValueError:
            return [text_block("错误: --qq 参数格式错误，应为逗号分隔的 QQ 号")]

        results = await browse_messages(
            msg_id=msg,
            ctx_id=ctx_id,
            before=before,
            after=after,
            since=since_dt,
            until=until_dt,
            limit=limit,
            qq_ids=qq_ids,
            name_pattern=name,
        )
        if not results:
            return [text_block("未找到消息")]
        actx = get_context()
        bot_cfg = getattr(actx.config, "bot", None)
        bot_qq = bot_cfg.qq if bot_cfg else None
        bot_name = actx.bot_name or None
        xml_output = await format_messages_to_xml(results, bot_qq=bot_qq, bot_name=bot_name)
        return [text_block(xml_output)]

    async def recent(
        self,
        *,
        ctx: int | None = None,
        after_msg: int | None = None,
        limit: int = 50,
        **_kw,
    ) -> ContentBlocks:
        ctx_id = _enforce_bot_ctx(ctx)
        if ctx_id is None:
            return [text_block("错误: 需要 --ctx 参数")]

        after_row_id = 0
        if after_msg is not None:
            after_row_id = await resolve_message_db_id(message_id=after_msg, ctx_id=ctx_id)
            if after_row_id == 0:
                return [text_block(f"错误: 未找到消息 {after_msg}")]

        results = await recent_messages(
            ctx_id,
            after_row_id=after_row_id,
            limit=limit,
        )
        if not results:
            return [text_block("未找到消息")]

        actx = get_context()
        bot_cfg = getattr(actx.config, "bot", None)
        bot_qq = bot_cfg.qq if bot_cfg else None
        bot_name = actx.bot_name or None
        xml_output = await format_messages_to_xml(results, bot_qq=bot_qq, bot_name=bot_name)
        return [text_block(xml_output)]

    async def read(
        self,
        *,
        forward_msg: str | None = None,
        **_kw,
    ) -> ContentBlocks:
        if not forward_msg:
            return [text_block("错误: 当前仅支持 --forward-msg <id>")]

        record = await ForwardRecord.filter(forward_id=forward_msg).first()
        if record is None:
            return [text_block(f"未找到合并转发 {forward_msg}")]

        actx = _get_optional_context()
        bot_cfg = getattr(actx.config, "bot", None) if actx else None
        bot_qq = bot_cfg.qq if bot_cfg else None
        bot_name = actx.bot_name if actx else None
        xml_output = await format_forward_nodes_to_xml(
            record.raw_nodes,
            bot_qq=bot_qq,
            bot_name=bot_name,
        )
        return [text_block(xml_output)]

    # ── Emoji reaction ───────────────────────────────────────────

    EMOJI_ALIASES: dict[str, str] = {
        "thumbsup": "76",   "heart": "66",      "laugh": "178",
        "cry": "5",         "cool": "16",       "doge": "179",
        "cute": "21",       "ok": "124",        "rose": "63",
        "fire": "128293",   "clap": "99",       "hug": "49",
        "think": "32",      "salute": "282",    "respect": "318",
        "celebrate": "320", "angry": "326",     "question": "10068",
        "press_button": "424",                  "button": "424",
    }

    async def react(
        self,
        *,
        msg: int | None = None,
        emoji: str | None = None,
        **_kw,
    ) -> ContentBlocks:
        if not msg:
            return [text_block("错误: 需要 --msg 参数")]
        if not emoji:
            names = ", ".join(sorted(self.EMOJI_ALIASES))
            return [text_block(f"错误: 需要 --emoji 参数。可用: {names}")]

        emoji_id = self.EMOJI_ALIASES.get(emoji.lower())
        if emoji_id is None:
            names = ", ".join(sorted(self.EMOJI_ALIASES))
            return [text_block(f"未知 emoji '{emoji}'。可用: {names}")]

        cfg = _get_config()
        api = cfg.daemon.recorder_api
        record = await MessageRecord.filter(message_id=msg).order_by("-id").first()
        if record is None:
            return [text_block(f"未找到消息 {msg}")]

        ctx = await Context.filter(id=record.ctx_id).first()
        if ctx is None:
            return [text_block(f"未找到消息 {msg} 的上下文")]

        body = {
            "message_type": ctx.type,
            "message": [{
                "type": "react",
                "data": {"message_id": str(msg), "emoji_id": emoji_id},
            }],
        }
        if ctx.type == "group":
            body["group_id"] = ctx.target_id
        else:
            body["user_id"] = ctx.target_id

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{api}/send_msg", json=body, headers={"X-Bot-Mode": "1"})
            if r.status_code != 200:
                return [text_block(f"表情回应失败: {r.text}")]
        return [text_block(f"已对消息 {msg} 回应 {emoji}")]

    async def list(
        self,
        *,
        _positional: Sequence[str] | None = None,
        gid: int | None = None,
        **_kw,
    ) -> ContentBlocks:
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
