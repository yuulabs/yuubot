"""Memory addon — save, recall, delete, show, config.

Runs in-process. Context isolation via AddonContext.ctx_id.
"""

from __future__ import annotations

from yuubot.addons import addon, get_context, text_block, ContentBlock
from yuubot.config import load_config
from yuubot.core.db import init_db, close_db
from yuubot.skills.mem import store as mem_store
from yuubot.skills.mem.forget import cleanup_stale, get_forget_days, set_forget_days


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


def _ctx_id() -> int | None:
    return get_context().ctx_id


def _is_bot() -> bool:
    return get_context().agent_name != ""


@addon("mem")
class MemAddon:

    async def save(
        self,
        *,
        _positional: list[str] | None = None,
        tags: str = "",
        scope: str = "private",
        **_kw,
    ) -> list[ContentBlock]:
        """Save a memory."""
        content = " ".join(_positional) if _positional else ""
        if not content:
            return [text_block("错误: 请提供记忆内容")]

        actx = get_context()
        if scope == "public" and actx.user_role and actx.user_role != "MASTER":
            return [text_block("错误: 仅 Master 可以保存 public 记忆")]

        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            source_user_id = actx.user_id
            mem_id = await mem_store.save(
                content, tag_list, _ctx_id(), cfg.memory.max_length,
                source_user_id=source_user_id,
                scope=scope,
            )
            tag_display = ", ".join(tag_list) if tag_list else "无"
            return [text_block(f"已保存记忆 [mem_id: {mem_id}]，标签: {tag_display}，scope: {scope}")]
        except ValueError as e:
            return [text_block(f"错误: {e}")]
        finally:
            await close_db()

    async def recall(
        self,
        *,
        _positional: list[str] | None = None,
        tags: str = "",
        limit: int = 10,
        **_kw,
    ) -> list[ContentBlock]:
        """Recall memories by keywords/tags."""
        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            show_all = not _is_bot()
            words = _positional if _positional else []
            tag_list = tags.split() if tags else []
            if not words and not tag_list:
                return [text_block("错误: words 和 tags 至少提供一个")]
            results = await mem_store.recall(words, tag_list, _ctx_id(), limit, show_all=show_all)
            if not results:
                return [text_block("未找到记忆")]
            lines = []
            for r in results:
                lines.append(f"[mem {r['id']}] (tags: {r['tags']}) {r['created_at']}")
                lines.append(f"  {r['content']}")
                lines.append("")
            lines.append(f"共找到 {len(results)} 条记忆")
            return [text_block("\n".join(lines))]
        finally:
            await close_db()

    async def delete(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        """Delete memories by IDs."""
        actx = get_context()
        if actx.user_role and actx.user_role != "MASTER":
            return [text_block("错误: 仅 Master 可以删除记忆")]

        ids_str = ",".join(_positional) if _positional else ""
        if not ids_str:
            return [text_block("错误: 请提供记忆 ID")]

        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
            count = await mem_store.delete(ids)
            return [text_block(f"已删除 {count} 条记忆: {', '.join(str(i) for i in ids)}")]
        finally:
            await close_db()

    async def show(
        self,
        *,
        tags: bool = False,
        **_kw,
    ) -> list[ContentBlock]:
        """Show memory tags."""
        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            show_all = not _is_bot()
            tag_list = await mem_store.show_tags(_ctx_id(), show_all=show_all)
            if not tag_list:
                return [text_block("暂无标签")]
            ctx_id = _ctx_id()
            header = "标签列表 (全部):" if show_all else (
                f"标签列表 (ctx {ctx_id}):" if ctx_id else "标签列表 (public only):"
            )
            lines = [header]
            for tag, count in tag_list:
                lines.append(f"  {tag}: {count} 条")
            return [text_block("\n".join(lines))]
        finally:
            await close_db()

    async def config(
        self,
        *,
        forget_days: int | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        """Configure memory system."""
        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            if forget_days is not None:
                await set_forget_days(forget_days)
                return [text_block(f"记忆保留天数已设为 {forget_days}")]
            else:
                days = await get_forget_days()
                return [text_block(f"当前记忆保留天数: {days}")]
        finally:
            await close_db()
