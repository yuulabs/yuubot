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
    """Hard-delete stale and expired-trashed memories. Returns total count deleted.

    Two cases:
    - Active memories not accessed within forget_days → hard delete
    - Trashed memories whose trashed_at is older than forget_days → hard delete
    """
    days = await get_forget_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Active memories past last_accessed cutoff
    stale = await Memory.filter(last_accessed__lt=cutoff, trashed_at__isnull=True).count()
    if stale > 0:
        await Memory.filter(last_accessed__lt=cutoff, trashed_at__isnull=True).delete()
        logger.info("Auto-forget: deleted {} stale memories (older than {} days)", stale, days)

    # Trashed memories whose trash date is past forget period
    expired_trash = await Memory.filter(trashed_at__lt=cutoff).count()
    if expired_trash > 0:
        await Memory.filter(trashed_at__lt=cutoff).delete()
        logger.info("Auto-forget: purged {} expired trashed memories", expired_trash)

    return stale + expired_trash
