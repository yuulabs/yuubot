"""IM skill CLI implementations."""

import json
import os
from datetime import datetime, timezone

import click
import httpx

from yuubot.config import load_config
from yuubot.core.db import init_db, close_db
from yuubot.skills.im.query import search_messages, browse_messages
from yuubot.skills.im.formatter import format_messages_to_xml


def _enforce_bot_ctx(ctx_id: int | None) -> int | None:
    """In bot mode, force ctx_id to the current context.

    Returns the enforced ctx_id, or the original if not in bot mode.
    """
    if os.environ.get("YUU_IN_BOT", "").lower() not in ("1", "true", "yes"):
        return ctx_id
    bot_ctx = os.environ.get("YUU_BOT_CTX", "")
    if not bot_ctx:
        return ctx_id
    forced = int(bot_ctx)
    if ctx_id is not None and ctx_id != forced:
        click.echo(f"安全限制: Bot 模式下只能查询当前上下文 (ctx={forced})")
    return forced


def _translate_docker_path(path: str) -> str:
    """Translate a Docker container path to the corresponding host path.

    - Strip ``file://`` URI prefix if present
    - ``/mnt/host/...`` → strip prefix (agent referencing host file via mount)
    - ``<container_home>/...`` → replace with host dir from env
    """
    # Strip file:// URI scheme
    if path.startswith("file://"):
        path = path[len("file://"):]

    if path.startswith("/mnt/host/"):
        return path[len("/mnt/host"):]  # keep leading /

    home_host_dir = os.environ.get("YUU_DOCKER_HOME_HOST_DIR", "")
    container_home = os.environ.get("YUU_DOCKER_HOME_DIR", "")
    if home_host_dir and container_home and path.startswith(container_home + "/"):
        return home_host_dir + path[len(container_home):]

    return path


def _translate_image_ref(val: str) -> str:
    """Translate an image file/url value: docker path → host path, ensure file:// for local."""
    translated = _translate_docker_path(val)
    # Local absolute paths need file:// prefix for NapCat
    if translated.startswith("/") and not translated.startswith("//"):
        return f"file://{translated}"
    return translated


def _normalize_segment(seg: dict) -> dict:
    """Normalize a flat segment to OneBot V11 format.

    Accepts both ``{"type":"text","text":"hi"}`` (flat) and
    ``{"type":"text","data":{"text":"hi"}}`` (OneBot V11).
    Always returns the OneBot V11 form.
    Translates Docker container paths to host paths for image segments.
    """
    if "data" in seg:
        seg = dict(seg)
        seg["data"] = dict(seg["data"])
        if seg["type"] == "image":
            for key in ("file", "url"):
                val = seg["data"].get(key, "")
                if val:
                    seg["data"][key] = _translate_image_ref(val)
        return seg
    seg_type = seg.get("type", "text")
    data = {k: v for k, v in seg.items() if k != "type"}
    if seg_type == "image":
        for key in ("file", "url"):
            val = data.get(key, "")
            if val:
                data[key] = _translate_image_ref(val)
    return {"type": seg_type, "data": data}


async def send_msg(
    msg: str,
    ctx_id: int | None,
    uid: int | None,
    gid: int | None,
    config_path: str | None,
    *,
    delay: float = 0,
) -> None:
    """Send a message via Recorder API → NapCat."""
    if delay > 0:
        import asyncio
        await asyncio.sleep(delay)
    cfg = load_config(config_path)
    api = cfg.daemon.recorder_api

    # Determine target
    if ctx_id is not None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{api}/ctx/{ctx_id}")
            if r.status_code != 200:
                click.echo(f"错误: ctx {ctx_id} 不存在")
                return
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
        click.echo("错误: 必须指定 --ctx, --uid 或 --gid")
        return

    # Parse message JSON
    try:
        segments = json.loads(msg)
    except json.JSONDecodeError as e:
        click.echo(f"错误: 消息 JSON 解析失败: {e}")
        raise SystemExit(1)

    # Normalize to OneBot V11 format: {"type": ..., "data": {...}}
    segments = [_normalize_segment(s) for s in segments]

    # Validate image segments: local file paths must exist
    for seg in segments:
        if seg.get("type") != "image":
            continue
        data = seg.get("data", {})
        for key in ("file", "url"):
            val = data.get(key, "")
            if not val:
                continue
            # Check local file:// paths
            if val.startswith("file://"):
                fpath = val[len("file://"):]
                if not os.path.isfile(fpath):
                    click.echo(f"错误: 图片文件不存在: {fpath}")
                    raise SystemExit(1)

    body = {"message_type": msg_type, "message": segments}
    if msg_type == "group":
        body["group_id"] = target_id
    else:
        body["user_id"] = target_id

    headers: dict[str, str] = {}
    if os.environ.get("YUU_IN_BOT", "").lower() in ("1", "true", "yes"):
        headers["X-Bot-Mode"] = "1"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{api}/send_msg", json=body, headers=headers)
        if r.status_code == 200:
            data = r.json()
            if "remaining" in data:
                click.echo(f"消息已发送 (剩余额度: {data['remaining']}/5)")
            else:
                click.echo("消息已发送")
        elif r.status_code == 429:
            click.echo("发送失败: 群聊限流，每分钟最多5条")
        else:
            click.echo(f"发送失败: {r.text}")


async def search_msg(
    keywords: str,
    ctx_id: int | None,
    limit: int,
    days: int,
    config_path: str | None,
) -> None:
    """Search messages in SQLite and output LLM-readable XML format."""
    ctx_id = _enforce_bot_ctx(ctx_id)
    cfg = load_config(config_path)
    await init_db(cfg.database.path)
    try:
        results = await search_messages(keywords, ctx_id, limit, days)
        if not results:
            click.echo("未找到消息")
            return

        # Output LLM-readable XML format
        xml_output = await format_messages_to_xml(results)
        click.echo(xml_output)
    finally:
        await close_db()


async def browse_msg(
    msg_id: int | None,
    ctx_id: int | None,
    before: int,
    after: int,
    since: str | None,
    until: str | None,
    limit: int,
    config_path: str | None,
) -> None:
    """Browse messages and output LLM-readable XML format."""
    ctx_id = _enforce_bot_ctx(ctx_id)
    cfg = load_config(config_path)
    await init_db(cfg.database.path)
    try:
        # Parse time strings
        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        results = await browse_messages(
            msg_id=msg_id,
            ctx_id=ctx_id,
            before=before,
            after=after,
            since=since_dt,
            until=until_dt,
            limit=limit,
        )

        if not results:
            click.echo("未找到消息")
            return

        # Output LLM-readable XML format
        xml_output = await format_messages_to_xml(results)
        click.echo(xml_output)
    finally:
        await close_db()


async def list_info(target: str, gid: int | None, config_path: str | None) -> None:
    """List friends/groups/members/contexts."""
    cfg = load_config(config_path)
    api = cfg.daemon.recorder_api

    async with httpx.AsyncClient(timeout=10) as client:
        if target == "friends":
            r = await client.get(f"{api}/get_friend_list")
            data = r.json().get("data", r.json())
            if isinstance(data, list):
                for f in data[:50]:
                    click.echo(f"{f.get('user_id', '?')} — {f.get('nickname', '?')}")
            else:
                click.echo(json.dumps(data, ensure_ascii=False, indent=2))

        elif target == "groups":
            r = await client.get(f"{api}/get_group_list")
            data = r.json().get("data", r.json())
            if isinstance(data, list):
                for g in data[:50]:
                    click.echo(f"{g.get('group_id', '?')} — {g.get('group_name', '?')}")
            else:
                click.echo(json.dumps(data, ensure_ascii=False, indent=2))

        elif target == "members":
            if gid is None:
                click.echo("错误: 需要 --gid 参数")
                return
            r = await client.get(f"{api}/get_group_member_list", params={"group_id": gid})
            data = r.json().get("data", r.json())
            if isinstance(data, list):
                for m in data[:100]:
                    click.echo(f"{m.get('user_id', '?')} — {m.get('nickname', '?')}")
            else:
                click.echo(json.dumps(data, ensure_ascii=False, indent=2))

        elif target == "contexts":
            r = await client.get(f"{api}/ctx")
            data = r.json()
            for c in data:
                click.echo(f"ctx {c['ctx_id']}: {c['type']} → {c['target_id']}")
