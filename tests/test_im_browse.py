from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yuubot.capabilities import CapabilityContext, execute
from yuubot.capabilities.im.query import recent_messages
from yuubot.core.models import Context, ImageSegment, MessageRecord, TextSegment, segments_to_json


async def test_im_browse_accepts_numeric_qq_argument(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_browse_messages(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("yuubot.capabilities.im.browse_messages", fake_browse_messages)

    result = await execute(
        "im browse --ctx 2 --qq 326598617",
        context=CapabilityContext(ctx_id=2),
    )

    assert result[0]["text"] == "未找到消息"
    assert captured["ctx_id"] == 2
    assert captured["qq_ids"] == [326598617]


async def test_im_recent_returns_messages_after_anchor(db):
    ctx = await Context.create(type="group", target_id=1000)
    now = datetime.now(timezone.utc)
    first = await MessageRecord.create(
        message_id=100,
        ctx_id=ctx.id,
        user_id=1,
        nickname="Alice",
        display_name="",
        content="第一条",
        raw_message=segments_to_json([TextSegment(text="第一条")]),
        timestamp=now,
        media_files=[],
    )
    second = await MessageRecord.create(
        message_id=101,
        ctx_id=ctx.id,
        user_id=2,
        nickname="Bob",
        display_name="",
        content="第二条",
        raw_message=segments_to_json([TextSegment(text="第二条")]),
        timestamp=now + timedelta(seconds=1),
        media_files=[],
    )

    queried = await recent_messages(ctx.id, after_row_id=first.id, limit=None)
    assert [msg["message_id"] for msg in queried] == [second.message_id]

    result = await execute(
        f"im recent --ctx {ctx.id} --after-msg 100 --limit 10",
        context=CapabilityContext(ctx_id=ctx.id),
    )
    assert "第二条" in result[0]["text"]
    assert "第一条" not in result[0]["text"]


async def test_im_recent_falls_back_to_remote_image_url_when_local_media_missing(db):
    ctx = await Context.create(type="group", target_id=1001)
    now = datetime.now(timezone.utc)
    image_url = "https://multimedia.nt.qq.com.cn/download?fileid=test"
    await MessageRecord.create(
        message_id=102,
        ctx_id=ctx.id,
        user_id=3,
        nickname="Carol",
        display_name="",
        content="[图片]",
        raw_message=segments_to_json([
            ImageSegment(
                url=image_url,
                file="test.jpg",
                local_path="",
            )
        ]),
        timestamp=now,
        media_files=[],
    )

    result = await execute(
        f"im recent --ctx {ctx.id} --limit 10",
        context=CapabilityContext(ctx_id=ctx.id),
    )

    assert image_url in result[0]["text"]
    assert 'file://[' not in result[0]["text"]
