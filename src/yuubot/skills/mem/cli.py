"""Memory skill CLI implementations."""

import click

from yuubot.config import load_config
from yuubot.core import env
from yuubot.core.db import init_db, close_db
from yuubot.skills.mem import store as mem_store
from yuubot.skills.mem.forget import cleanup_stale, get_forget_days, set_forget_days


def _get_ctx_id() -> int | None:
    """Read ctx_id from environment. Always use this instead of CLI args."""
    raw = env.get(env.BOT_CTX)
    return int(raw) if raw else None


async def save_memory(content: str, tags_str: str, scope: str, config_path: str | None) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        ctx_id = _get_ctx_id()
        # Only Master can save public memories
        if scope == "public":
            role = env.get(env.USER_ROLE)
            if role and role != "MASTER":
                click.echo("错误: 仅 Master 可以保存 public 记忆")
                return
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        raw_uid = env.get(env.USER_ID)
        source_user_id = int(raw_uid) if raw_uid else None
        mem_id = await mem_store.save(
            content, tags, ctx_id, cfg.memory.max_length,
            source_user_id=source_user_id,
            scope=scope,
        )
        tag_display = ", ".join(tags) if tags else "无"
        click.echo(f"已保存记忆 [mem_id: {mem_id}]，标签: {tag_display}，scope: {scope}")
    except ValueError as e:
        click.echo(f"错误: {e}")
    finally:
        await close_db()


async def recall_memory(
    words_str: str, tags_str: str, limit: int, config_path: str | None
) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        ctx_id = _get_ctx_id()
        words = words_str.split() if words_str else []
        tags = tags_str.split() if tags_str else []
        if not words and not tags:
            click.echo("错误: words 和 tags 至少提供一个")
            return
        results = await mem_store.recall(words, tags, ctx_id, limit)
        if not results:
            click.echo("未找到记忆")
            return
        for r in results:
            click.echo(f"[mem {r['id']}] (tags: {r['tags']}) {r['created_at']}")
            click.echo(f"  {r['content']}")
            click.echo()
        click.echo(f"共找到 {len(results)} 条记忆")
    finally:
        await close_db()


async def delete_memory(ids_str: str, config_path: str | None) -> None:
    # Permission check: only Master can delete
    role = env.get(env.USER_ROLE)
    if role and role != "MASTER":
        click.echo("错误: 仅 Master 可以删除记忆")
        return

    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
        count = await mem_store.delete(ids)
        click.echo(f"已删除 {count} 条记忆: {', '.join(str(i) for i in ids)}")
    finally:
        await close_db()


async def show_tags(config_path: str | None) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        ctx_id = _get_ctx_id()
        tags = await mem_store.show_tags(ctx_id)
        if not tags:
            click.echo("暂无标签")
            return
        header = f"标签列表 (ctx {ctx_id}):" if ctx_id else "标签列表 (public only):"
        click.echo(header)
        for tag, count in tags:
            click.echo(f"  {tag}: {count} 条")
    finally:
        await close_db()


async def configure_memory(forget_days: int | None, config_path: str | None) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        if forget_days is not None:
            await set_forget_days(forget_days)
            click.echo(f"记忆保留天数已设为 {forget_days}")
        else:
            days = await get_forget_days()
            click.echo(f"当前记忆保留天数: {days}")
    finally:
        await close_db()
