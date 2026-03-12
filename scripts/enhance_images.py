#!/usr/bin/env python3
"""Batch enhance image descriptions in the database.

Two operations:
1. Deduplicate: find images with identical file content (SHA256), keep the
   best description, delete the rest.
2. Enhance: re-describe images with missing or low-quality descriptions using
   the Gemini vision model via OpenRouter.

Usage:
    python scripts/enhance_images.py [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import os
import sys
from pathlib import Path

# Add project src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _is_poor_description(desc: str) -> bool:
    """Return True if description is missing or low quality."""
    if not desc or len(desc.strip()) < 10:
        return True
    # Source-only descriptions have no retrieval value
    source_patterns = ["分享的图片", "分享的动图", "发的图片", "发的动图", "的图片", "的动图"]
    stripped = desc.strip()
    if len(stripped) < 30 and any(p in stripped for p in source_patterns):
        return True
    return False


async def normalize_paths(dry_run: bool = False) -> int:
    """Strip file:// prefix from all image paths in the database."""
    from yuubot.core.models import ImageEntry

    entries = await ImageEntry.filter(local_path__startswith="file://")
    fixed = 0
    for entry in entries:
        new_path = entry.local_path.removeprefix("file://")
        print(f"  {entry.local_path} -> {new_path}")
        if not dry_run:
            entry.local_path = new_path
            await entry.save()
        fixed += 1
    return fixed


def _sha256(path: str) -> str | None:
    """Compute SHA256 of a file. Returns None if file not readable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


async def deduplicate_images(dry_run: bool = False) -> int:
    """Find duplicate images by SHA256 hash, keep best description, delete rest."""
    from yuubot.core.models import ImageEntry

    entries = await ImageEntry.all()
    hash_groups: dict[str, list[ImageEntry]] = {}

    for entry in entries:
        img_path = entry.local_path.removeprefix("file://")
        h = _sha256(img_path)
        if h is None:
            continue
        hash_groups.setdefault(h, []).append(entry)

    deleted = 0
    for h, group in hash_groups.items():
        if len(group) < 2:
            continue

        # Pick the entry with the best description (longest non-source desc)
        def score(e: ImageEntry) -> int:
            if _is_poor_description(e.description):
                return 0
            return len(e.description)

        group.sort(key=score, reverse=True)
        keep = group[0]
        to_delete = group[1:]

        print(f"Duplicate group ({len(group)} files, hash={h[:12]}):")
        print(f"  KEEP  [{keep.id}] {keep.local_path}")
        print(f"        desc: {keep.description[:80]!r}")
        for e in to_delete:
            print(f"  DEL   [{e.id}] {e.local_path}")
            print(f"        desc: {e.description[:80]!r}")
            if not dry_run:
                await e.delete()
                deleted += 1
            else:
                deleted += 1

    return deleted


async def _call_vision_llm(image_path: str) -> str:
    """Call Gemini via OpenRouter to describe an image."""
    import yuullm
    from yuullm.providers import OpenAIChatCompletionProvider

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    p = Path(image_path)
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }
    mime = mime_map.get(p.suffix.lower(), "image/jpeg")
    data = base64.b64encode(p.read_bytes()).decode()
    data_uri = f"data:{mime};base64,{data}"

    provider = OpenAIChatCompletionProvider(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    client = yuullm.YLLMClient(
        provider=provider,
        default_model="google/gemini-3.1-flash-lite-preview",
    )

    messages = [
        yuullm.system(
            "你是一个图片描述助手。请用中文简洁地描述图片内容。\n"
            "要求：\n"
            "- 纯文本，不要使用markdown格式、编号、标题\n"
            "- 描述画面内容、情绪氛围、适用场景\n"
            "- 简洁但信息完整，让人能通过描述搜索到这张图\n"
            "- 直接描述，不要以'这张图片'开头"
        ),
        yuullm.user(
            "请描述这张图片：",
            {"type": "image_url", "image_url": {"url": data_uri}},
        ),
    ]

    stream, _ = await client.stream(messages)
    parts: list[str] = []
    async for item in stream:
        if isinstance(item, yuullm.Response) and isinstance(item.item, str):
            parts.append(item.item)
    return "".join(parts).strip()


async def enhance_descriptions(dry_run: bool = False, force: bool = False, limit: int = 20) -> int:
    """Re-describe images with poor or missing descriptions.

    Only processes images that are already in the database (shown by `img list`).
    Uses the same limit as `img list` to avoid processing too many images.
    """
    from yuubot.skills.img import store

    # Use img.search() with no query to get recent images (same as `img list`)
    results = await store.search(limit=limit)

    to_enhance = [
        r for r in results
        if force or _is_poor_description(r["description"])
    ]

    if not to_enhance:
        print("No images need enhancement.")
        return 0

    print(f"Found {len(to_enhance)} images to enhance (from last {limit} in library):")
    for r in to_enhance:
        print(f"  [{r['id']}] {r['local_path']}")
        print(f"        current desc: {r['description'][:60]!r}")

    if dry_run:
        return len(to_enhance)

    enhanced = 0
    for r in to_enhance:
        img_path = r["local_path"].removeprefix("file://")
        if not Path(img_path).is_file():
            print(f"  SKIP [{r['id']}] file not found: {img_path}")
            continue
        try:
            desc = await _call_vision_llm(img_path)
            await store.save(r["local_path"], description=desc, tags=r["tags"])
            enhanced += 1
            print(f"  OK   [{r['id']}] {desc[:80]!r}")
        except Exception as ex:
            print(f"  ERR  [{r['id']}] {ex}")

    return enhanced


async def main(dry_run: bool, force: bool, limit: int) -> None:
    from yuubot.config import load_config
    from yuubot.core.db import init_db, close_db

    cfg = load_config(None)
    from pathlib import Path as _Path
    db_path = str(_Path(cfg.database.path).expanduser())
    await init_db(db_path, simple_ext=cfg.database.simple_ext)

    try:
        print("=== Path Normalization ===")
        n_norm = await normalize_paths(dry_run=dry_run)
        action = "would normalize" if dry_run else "normalized"
        print(f"{action} {n_norm} path(s)\n")

        print("=== Deduplication ===")
        n_dedup = await deduplicate_images(dry_run=dry_run)
        action = "would delete" if dry_run else "deleted"
        print(f"{action} {n_dedup} duplicate(s)\n")

        print("=== Description Enhancement ===")
        n_enhanced = await enhance_descriptions(dry_run=dry_run, force=force, limit=limit)
        action = "would enhance" if dry_run else "enhanced"
        print(f"\n{action} {n_enhanced} image(s)")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enhance image descriptions in the database.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--force", action="store_true", help="Re-describe all images, not just poor ones")
    parser.add_argument("--limit", type=int, default=20, help="Max number of images to process (default: 20)")
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, force=args.force, limit=args.limit))
