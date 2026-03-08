"""Image library skill CLI implementations."""

import click

from yuubot.config import load_config
from yuubot.core.db import init_db, close_db
from yuubot.skills.img import store as img_store


async def save_image(
    path: str, desc: str, tags_str: str, config_path: str | None
) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        img_id = await img_store.save(path, description=desc, tags=tags)
        tag_display = ", ".join(tags) if tags else "无"
        click.echo(f"已保存图片 [id: {img_id}]，描述: {desc}，标签: {tag_display}")
    finally:
        await close_db()


async def search_image(
    query: str, tags_str: str, limit: int, config_path: str | None
) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else None
        results = await img_store.search(query=query, tags=tags, limit=limit)
        if not results:
            click.echo("未找到匹配的图片")
            return
        for r in results:
            tag_display = ", ".join(r["tags"]) if r["tags"] else ""
            click.echo(f"[img {r['id']}] {r['local_path']}")
            if r["description"]:
                click.echo(f"  描述: {r['description']}")
            if tag_display:
                click.echo(f"  标签: {tag_display}")
            click.echo()
        click.echo(f"共找到 {len(results)} 张图片")
    finally:
        await close_db()


async def delete_image(image_id: int, config_path: str | None) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        ok = await img_store.delete(image_id)
        if ok:
            click.echo(f"已删除图片 [id: {image_id}]")
        else:
            click.echo(f"未找到图片 [id: {image_id}]")
    finally:
        await close_db()


async def list_images(show_tags: bool, limit: int, config_path: str | None) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        if show_tags:
            tags = await img_store.list_tags()
            if not tags:
                click.echo("暂无标签")
                return
            click.echo("图片标签列表:")
            for tag, count in tags:
                click.echo(f"  {tag}: {count} 张")
        else:
            results = await img_store.search(limit=limit)
            if not results:
                click.echo("图片库为空")
                return
            for r in results:
                tag_display = ", ".join(r["tags"]) if r["tags"] else ""
                desc = r["description"][:40] if r["description"] else ""
                click.echo(f"[img {r['id']}] {r['local_path']}  {desc}  {tag_display}")
            click.echo(f"共 {len(results)} 张图片")
    finally:
        await close_db()
