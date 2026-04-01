from __future__ import annotations

from datetime import datetime, timezone

from yuubot.capabilities.im.formatter import format_messages_to_xml
from yuubot.core.models import Context, MessageRecord, TextSegment, segments_to_json


async def test_format_messages_to_xml_uses_real_name_and_display_name(db):
    ctx = await Context.create(type="group", target_id=1000)
    record = await MessageRecord.create(
        message_id=321,
        ctx_id=ctx.id,
        user_id=20001,
        nickname="PriNone",
        display_name="morphology",
        content="你好",
        raw_message=segments_to_json([TextSegment(text="你好")]),
        timestamp=datetime.now(timezone.utc),
        media_files=[],
    )

    text = await format_messages_to_xml([
        {
            "db_id": record.id,
            "message_id": record.message_id,
            "timestamp": record.timestamp,
            "user_id": record.user_id,
            "nickname": record.nickname,
            "display_name": record.display_name,
            "ctx_id": ctx.id,
            "content": record.content,
            "raw_message": record.raw_message,
            "media_files": record.media_files,
        }
    ])

    assert 'name="PriNone"' in text
    assert 'display_name="morphology"' in text
