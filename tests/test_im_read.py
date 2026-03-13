from __future__ import annotations

import json
from datetime import datetime, timezone

from yuubot.capabilities.im import ImCapability
from yuubot.core.models import Context, ForwardRecord, MessageRecord, segments_to_json
from yuubot.core.models import ForwardSegment, TextSegment


async def test_im_read_forward_msg_renders_inner_messages_without_recursive_expand(db):
    ctx = await Context.create(type="group", target_id=1000)
    await MessageRecord.create(
        message_id=321,
        ctx_id=ctx.id,
        user_id=20001,
        nickname="Alice",
        display_name="",
        content="外层消息",
        raw_message=segments_to_json([TextSegment(text="外层消息")]),
        timestamp=datetime.now(timezone.utc),
        media_files=[],
    )
    await ForwardRecord.create(
        forward_id="fw-1",
        summary="外层消息",
        raw_nodes=json.dumps([
            {
                "message_id": 321,
                "user_id": 20001,
                "nickname": "Alice",
                "display_name": "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "raw_message": segments_to_json([
                    TextSegment(text="外层消息"),
                    ForwardSegment(id="fw-2", summary="内层摘要"),
                ]),
                "media_files": [],
            },
        ], ensure_ascii=False),
        source_message_id=999,
        source_ctx_id=ctx.id,
    )

    cap = ImCapability()
    result = await cap.read(forward_msg="fw-1")

    assert len(result) == 1
    text = result[0]["text"]
    assert '<msg id="321"' in text
    assert "外层消息" in text
    assert '<forward_msg id="fw-2" summary="内层摘要"/>' in text

