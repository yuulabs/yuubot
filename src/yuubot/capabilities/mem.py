"""Memory capability — save, recall, delete, show, config."""

from __future__ import annotations

from yuubot.capabilities import capability, get_context, text_block, ContentBlock
from yuubot.config import load_config
from yuubot.skills.mem import store as mem_store
from yuubot.skills.mem.forget import get_forget_days, set_forget_days


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


def _ctx_id() -> int | None:
    return get_context().ctx_id


def _is_bot() -> bool:
    return get_context().agent_name != ""


@capability("mem")
class MemCapability:

    async def save(
        self,
        *,
        _positional: list[str] | None = None,
        tags: str = "",
        scope: str = "private",
        **_kw,
    ) -> list[ContentBlock]:
        content = " ".join(_positional) if _positional else ""
        if not content:
            return [text_block("错误: 请提供记忆内容")]

        actx = get_context()
        if scope == "public" and actx.user_role and actx.user_role != "MASTER":
            return [text_block("错误: 仅 Master 可以保存 public 记忆")]

        cfg = _get_config()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        source_user_id = actx.user_id
        mem_id = await mem_store.save(
            content, tag_list, _ctx_id(), cfg.memory.max_length,
            source_user_id=source_user_id,
            scope=scope,
        )
        tag_display = ", ".join(tag_list) if tag_list else "无"
        return [text_block(f"已保存记忆 [mem_id: {mem_id}]，标签: {tag_display}，scope: {scope}")]

    async def recall(
        self,
        *,
        _positional: list[str] | None = None,
        tags: str = "",
        limit: int = 10,
        **_kw,
    ) -> list[ContentBlock]:
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

    async def delete(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        """Soft-delete (trash) memories. Only mem_curator agent may call this in-bot."""
        actx = get_context()
        if _is_bot() and actx.agent_name != "mem_curator":
            raise PermissionError(f"Agent '{actx.agent_name}' 无权调用 mem delete。仅 mem_curator 可删除记忆。")

        ids_str = ",".join(_positional) if _positional else ""
        if not ids_str:
            return [text_block("错误: 请提供记忆 ID")]

        ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
        count = await mem_store.trash(ids)
        return [text_block(f"已移入垃圾桶 {count} 条记忆 (ID: {', '.join(str(i) for i in ids)})。可用 mem restore 回滚。")]

    async def restore(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        """Restore trashed memories. Curator agent or CLI only."""
        actx = get_context()
        if _is_bot() and actx.agent_name != "mem_curator":
            raise PermissionError(f"Agent '{actx.agent_name}' 无权调用 mem restore。仅 mem_curator 可回滚记忆。")

        ids_str = ",".join(_positional) if _positional else ""
        if not ids_str:
            return [text_block("错误: 请提供记忆 ID")]

        ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
        count = await mem_store.restore(ids)
        return [text_block(f"已恢复 {count} 条记忆 (ID: {', '.join(str(i) for i in ids)})")]

    async def show(
        self,
        *,
        tags: bool = False,
        **_kw,
    ) -> list[ContentBlock]:
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

    async def config(
        self,
        *,
        forget_days: int | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        if forget_days is not None:
            await set_forget_days(forget_days)
            return [text_block(f"记忆保留天数已设为 {forget_days}")]
        else:
            days = await get_forget_days()
            return [text_block(f"当前记忆保留天数: {days}")]
