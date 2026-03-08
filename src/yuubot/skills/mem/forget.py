"""Auto-forget — clean up stale memories."""

import logging
from datetime import datetime, timedelta, timezone

from yuubot.core.models import Memory, MemoryConfigKV

log = logging.getLogger(__name__)


async def get_forget_days() -> int:
    row = await MemoryConfigKV.filter(key="forget_days").first()
    return int(row.value) if row else 90


async def set_forget_days(days: int) -> None:
    await MemoryConfigKV.update_or_create(
        defaults={"value": str(days)},
        key="forget_days",
    )


async def cleanup_stale() -> int:
    """Delete memories not accessed within forget_days. Returns count deleted."""
    days = await get_forget_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    count = await Memory.filter(last_accessed__lt=cutoff).count()
    if count > 0:
        await Memory.filter(last_accessed__lt=cutoff).delete()
        log.info("Auto-forget: deleted %d stale memories (older than %d days)", count, days)

    return count
