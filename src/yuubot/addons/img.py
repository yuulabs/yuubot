"""Image library addon — save, search, delete, list."""

from __future__ import annotations

from yuubot.addons import addon, get_context, text_block, ContentBlock
from yuubot.config import load_config
from yuubot.core.db import init_db, close_db
from yuubot.skills.img import store as img_store


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


@addon("img")
class ImgAddon:

    async def save(
        self,
        *,
        _positional: list[str] | None = None,
        desc: str = "",
        tags: str = "",
        **_kw,
    ) -> list[ContentBlock]:
        """Save an image to the library."""
        path = _positional[0] if _positional else ""
        if not path:
            return [text_block("错误: 请提供图片路径")]

        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            img_id = await img_store.save(path, description=desc, tags=tag_list)
            tag_display = ", ".join(tag_list) if tag_list else "无"
            return [text_block(f"已保存图片 [id: {img_id}]，描述: {desc}，标签: {tag_display}")]
        finally:
            await close_db()

    async def search(
        self,
        *,
        _positional: list[str] | None = None,
        tags: str = "",
        limit: int = 10,
        **_kw,
    ) -> list[ContentBlock]:
        """Search images by description/tags."""
        query = " ".join(_positional) if _positional else ""
        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            results = await img_store.search(query=query, tags=tag_list, limit=limit)
            if not results:
                return [text_block("未找到匹配的图片")]
            lines = []
            for r in results:
                tag_display = ", ".join(r["tags"]) if r["tags"] else ""
                lines.append(f"[img {r['id']}] {r['local_path']}")
                if r["description"]:
                    lines.append(f"  描述: {r['description']}")
                if tag_display:
                    lines.append(f"  标签: {tag_display}")
                lines.append("")
            lines.append(f"共找到 {len(results)} 张图片")
            return [text_block("\n".join(lines))]
        finally:
            await close_db()

    async def delete(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        """Delete an image by ID."""
        if not _positional:
            return [text_block("错误: 请提供图片 ID")]
        image_id = int(_positional[0])
        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            ok = await img_store.delete(image_id)
            if ok:
                return [text_block(f"已删除图片 [id: {image_id}]")]
            return [text_block(f"未找到图片 [id: {image_id}]")]
        finally:
            await close_db()

    async def list(
        self,
        *,
        tags: bool = False,
        limit: int = 20,
        **_kw,
    ) -> list[ContentBlock]:
        """List images or tags."""
        cfg = _get_config()
        await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
        try:
            if tags:
                tag_list = await img_store.list_tags()
                if not tag_list:
                    return [text_block("暂无标签")]
                lines = ["图片标签列表:"]
                for tag, count in tag_list:
                    lines.append(f"  {tag}: {count} 张")
                return [text_block("\n".join(lines))]
            else:
                results = await img_store.search(limit=limit)
                if not results:
                    return [text_block("图片库为空")]
                lines = []
                for r in results:
                    tag_display = ", ".join(r["tags"]) if r["tags"] else ""
                    desc = r["description"][:40] if r["description"] else ""
                    lines.append(f"[img {r['id']}] {r['local_path']}  {desc}  {tag_display}")
                lines.append(f"共 {len(results)} 张图片")
                return [text_block("\n".join(lines))]
        finally:
            await close_db()
