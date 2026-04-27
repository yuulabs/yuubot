"""Persistent path bootstrapping — mirrors paths under a volume-mounted data dir."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger


async def setup_persistent_paths(persistent_paths: list[str], persist_base: str = "") -> None:
    """Idempotent: mirror each path under persist_base via symlink."""
    if not persistent_paths:
        return

    base = Path(persist_base or "data/yuubot/persist").expanduser()

    for raw in persistent_paths:
        internal = Path(raw).expanduser()
        try:
            resolved = internal.resolve()
            mirror = base / str(resolved).lstrip("/")
        except Exception:
            logger.warning("admin persist: cannot resolve {!r}, skipping", raw)
            continue

        if internal.is_symlink():
            logger.debug("admin persist: {} already symlinked, skipping", internal)
            continue

        try:
            if not mirror.exists() and internal.is_dir():
                # First deploy: copy contents to data/, replace with symlink
                mirror.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(internal, mirror)
                shutil.rmtree(internal)
                internal.symlink_to(mirror)
                logger.info("admin persist: {} → {} (first deploy)", internal, mirror)

            elif mirror.exists() and internal.is_dir():
                # Container rebuilt: real dir exists but mirror already has content
                shutil.rmtree(internal)
                internal.symlink_to(mirror)
                logger.info("admin persist: {} → {} (container rebuild)", internal, mirror)

            else:
                # Path doesn't exist yet: create mirror dir and symlink
                mirror.mkdir(parents=True, exist_ok=True)
                internal.parent.mkdir(parents=True, exist_ok=True)
                internal.symlink_to(mirror)
                logger.info("admin persist: {} → {} (new path)", internal, mirror)

        except Exception as exc:
            logger.error("admin persist: failed to set up {!r}: {}", raw, exc)
