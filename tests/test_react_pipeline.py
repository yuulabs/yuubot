from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import respx

from yuubot.capabilities import CapabilityContext, execute
from yuubot.core.context import ContextManager
from yuubot.core.models import Context, GroupSetting, MessageRecord, TextSegment, segments_to_json
from yuubot.recorder.api import create_api
from yuubot.recorder.server import load_muted_ctxs
from tests.mocks import mock_recorder_api


async def test_im_react_routes_via_send_msg(db, yuubot_config):
    ctx = await Context.create(type="group", target_id=1000)
    await MessageRecord.create(
        message_id=12345,
        ctx_id=ctx.id,
        user_id=20001,
        nickname="Alice",
        display_name="",
        content="hello",
        raw_message=segments_to_json([TextSegment(text="hello")]),
        timestamp=datetime.now(timezone.utc),
        media_files=[],
    )

    with mock_recorder_api(str(yuubot_config.daemon.recorder_api)) as sent:
        result = await execute(
            "im react --msg 12345 --emoji heart",
            context=CapabilityContext(config=yuubot_config, ctx_id=ctx.id),
        )

    assert result[0]["text"] == "已对消息 12345 回应 heart"
    assert sent == [{
        "message_type": "group",
        "group_id": 1000,
        "message": [{
            "type": "react",
            "data": {"message_id": "12345", "emoji_id": "66"},
        }],
    }]


async def test_recorder_send_msg_extracts_react_segment(db):
    ctx_mgr = ContextManager()
    await ctx_mgr.load()
    app = create_api("http://napcat.test", ctx_mgr, shutdown_event=asyncio.Event(), bot_qq=99999)

    with respx.mock(assert_all_called=False) as router:
        send_route = router.post("http://napcat.test/send_msg").mock(
            return_value=httpx.Response(200, json={"status": "ok", "retcode": 0}),
        )
        react_route = router.post("http://napcat.test/set_msg_emoji_like").mock(
            return_value=httpx.Response(200, json={"status": "ok", "retcode": 0}),
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/send_msg",
                json={
                    "message_type": "group",
                    "group_id": 1000,
                    "message": [{
                        "type": "react",
                        "data": {"message_id": "12345", "emoji_id": "66"},
                    }],
                },
            )

    assert response.status_code == 200
    assert response.json()["remaining"] == 4
    assert not send_route.called
    assert react_route.called
    assert react_route.calls[0].request.content == b'{"message_id":"12345","emoji_id":"66"}'


async def test_load_muted_ctxs_restores_disabled_groups(db):
    await GroupSetting.filter(group_id=1000).update(bot_enabled=False)

    ctx_mgr = ContextManager()
    await ctx_mgr.load()

    muted_ctxs = await load_muted_ctxs(ctx_mgr)
    group_ctx = ctx_mgr.lookup("group", 1000)

    assert group_ctx is not None
    assert muted_ctxs == {group_ctx}
