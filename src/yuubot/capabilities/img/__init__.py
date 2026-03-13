"""Image library capability — save, search, delete, list."""

from __future__ import annotations

from collections.abc import Sequence

from yuubot.capabilities import capability, text_block, ContentBlock, uri_to_path
from . import store as img_store

type ContentBlocks = list[ContentBlock]


@capability("img")
class ImgCapability:

    async def save(
        self,
        *,
        _positional: Sequence[str] | None = None,
        desc: str = "",
        tags: str = "",
        **_kw,
    ) -> ContentBlocks:
        path = uri_to_path(_positional[0]) if _positional else ""
        if not path:
            return [text_block("错误: 请提供图片路径")]

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        img_id = await img_store.save(path, description=desc, tags=tag_list)
        return [text_block(f"已保存 [img {img_id}]")]

    async def search(
        self,
        *,
        _positional: Sequence[str] | None = None,
        tags: str = "",
        limit: int = 10,
        **_kw,
    ) -> ContentBlocks:
        query = " ".join(_positional) if _positional else ""
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

    async def delete(
        self,
        *,
        _positional: Sequence[str] | None = None,
        **_kw,
    ) -> ContentBlocks:
        if not _positional:
            return [text_block("错误: 请提供图片 ID")]
        image_id = int(_positional[0])
        ok = await img_store.delete(image_id)
        if ok:
            return [text_block(f"已删除图片 [id: {image_id}]")]
        return [text_block(f"未找到图片 [id: {image_id}]")]

    async def list(
        self,
        *,
        tags: bool = False,
        limit: int = 20,
        **_kw,
    ) -> ContentBlocks:
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
                desc = r["description"] or ""
                lines.append(f"[img {r['id']}] {r['local_path']}  {desc}  {tag_display}")
            lines.append(f"共 {len(results)} 张图片")
            return [text_block("\n".join(lines))]
