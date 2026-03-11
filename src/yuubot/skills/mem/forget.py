"""Auto-forget — clean up stale memories."""

from datetime import datetime, timedelta, timezone

from yuubot.core.models import Memory, MemoryConfigKV

from loguru import logger


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
        logger.info("Auto-forget: deleted {} stale memories (older than {} days)", count, days)

    return count
