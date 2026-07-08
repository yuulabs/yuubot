from __future__ import annotations

import pytest

import yb.tasks


@pytest.mark.asyncio
async def test_manual_submit_requires_ttl() -> None:
    with pytest.raises(ValueError, match="requires ttl_s"):
        await yb.tasks.submit("name", "true", "intro", delivery="manual")


@pytest.mark.asyncio
async def test_submit_rejects_ttl_over_one_hour() -> None:
    with pytest.raises(ValueError, match="<= 3600"):
        await yb.tasks.submit("name", "true", "intro", delivery="conversation", ttl_s=3601)
