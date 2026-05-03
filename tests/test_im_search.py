from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yuubot.core.models import Context, MessageRecord
from yuubot.services.im import ImService


@pytest.mark.asyncio
async def test_search_messages_quotes_slash_command_terms(db) -> None:
    await Context.get_or_create(id=901, defaults={"type": "private", "target_id": 10001})
    await MessageRecord.create(
        message_id=90001,
        ctx_id=901,
        user_id=10001,
        nickname="tester",
        display_name="tester",
        content="/yllm earlier command",
        raw_message='[{"type":"text","data":{"text":"/yllm earlier command"}}]',
        timestamp=datetime.now(timezone.utc),
        media_files=[],
    )

    rows = await ImService().search_messages(
        {"query": "/yllm", "ctx_id": 901, "bot_kind": "master", "limit": 10}
    )

    assert [row["content"] for row in rows] == ["/yllm earlier command"]
