"""Image library skill CLI — thin wrapper delegating to ImgAddon."""

import click

from yuubot.config import load_config
from yuubot.core.db import init_db, close_db


def _print_blocks(blocks: list[dict]) -> None:
    """Print addon ContentBlock results to terminal."""
    for b in blocks:
        if b.get("type") == "text":
            click.echo(b["text"])


async def _with_db(config_path: str | None, coro_fn):
    """Init DB, run coro_fn, close DB."""
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        return await coro_fn()
    finally:
        await close_db()


async def save_image(
    path: str, desc: str, tags_str: str, config_path: str | None
) -> None:
    from yuubot.addons.img import ImgAddon
    addon = ImgAddon()

    async def run():
        return await addon.save(_positional=[path], desc=desc, tags=tags_str)

    blocks = await _with_db(config_path, run)
    _print_blocks(blocks)


async def search_image(
    query: str, tags_str: str, limit: int, config_path: str | None
) -> None:
    from yuubot.addons.img import ImgAddon
    addon = ImgAddon()

    async def run():
        return await addon.search(
            _positional=[query] if query else None,
            tags=tags_str, limit=limit,
        )

    blocks = await _with_db(config_path, run)
    _print_blocks(blocks)


async def delete_image(image_id: int, config_path: str | None) -> None:
    from yuubot.addons.img import ImgAddon
    addon = ImgAddon()

    async def run():
        return await addon.delete(_positional=[str(image_id)])

    blocks = await _with_db(config_path, run)
    _print_blocks(blocks)


async def list_images(
    show_tags: bool, limit: int, config_path: str | None
) -> None:
    from yuubot.addons.img import ImgAddon
    addon = ImgAddon()

    async def run():
        return await addon.list(tags=show_tags, limit=limit)

    blocks = await _with_db(config_path, run)
    _print_blocks(blocks)
