import json

import httpx
import pytest
import respx

from yuubot.core.models import ForwardRecord
from yuubot.recorder.forward import ForwardResolver


@pytest.mark.usefixtures("db")
async def test_forward_resolver_uses_inline_nested_forward_payload() -> None:
    requested_ids: list[str] = []

    outer_id = "outer-forward"
    inner_id = "inner-forward"
    inline_nodes = [
        {
            "user_id": 200,
            "time": 1777350001,
            "message_id": 11,
            "sender": {"nickname": "inner"},
            "message": [{"type": "text", "data": {"text": "inner text"}}],
        },
        {
            "user_id": 201,
            "time": 1777350002,
            "message_id": 12,
            "sender": {"nickname": "inner image"},
            "message": [{"type": "image", "data": {"file": "pic.jpg"}}],
        },
    ]
    outer_payload = {
        "status": "ok",
        "retcode": 0,
        "data": {
            "messages": [
                {
                    "user_id": 100,
                    "time": 1777350000,
                    "message_id": 10,
                    "sender": {"nickname": "outer"},
                    "message": [
                        {
                            "type": "forward",
                            "data": {"id": inner_id, "content": inline_nodes},
                        },
                    ],
                },
            ],
        },
    }
    inner_failed_payload = {
        "status": "failed",
        "retcode": 200,
        "data": None,
        "message": "消息已过期或者为内层消息，无法获取转发消息",
    }

    def get_forward_msg(request: httpx.Request) -> httpx.Response:
        forward_id = request.url.params["id"]
        requested_ids.append(forward_id)
        if forward_id == outer_id:
            return httpx.Response(200, json=outer_payload)
        if forward_id == inner_id:
            return httpx.Response(200, json=inner_failed_payload)
        return httpx.Response(404)

    with respx.mock(assert_all_called=False) as router:
        router.get("http://napcat/get_forward_msg").mock(side_effect=get_forward_msg)
        resolver = ForwardResolver("http://napcat")
        try:
            resolved = await resolver.resolve(
                outer_id,
                source_message_id=123,
                source_ctx_id=4,
            )
        finally:
            await resolver.close()

    assert requested_ids == [outer_id]
    assert resolved is not None
    assert resolved["summary"] == "[合并转发:inner-forward:inner text / [图片]]"
    children = resolved["log_nodes"][0]["children"]
    assert [child["content"] for child in children] == ["inner text", "[图片]"]
    assert children[0]["raw_message"] == json.dumps(
        [{"type": "text", "text": "inner text"}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert children[1]["raw_message"] == json.dumps(
        [{"type": "image", "url": "", "file": "pic.jpg", "local_path": ""}],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    outer_record = await ForwardRecord.get(forward_id=outer_id)
    inner_record = await ForwardRecord.get(forward_id=inner_id)
    assert outer_record.summary == "[合并转发:inner-forward:inner text / [图片]]"
    assert inner_record.summary == "inner text / [图片]"


@pytest.mark.usefixtures("db")
async def test_forward_resolver_ignores_failed_null_payload() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://napcat/get_forward_msg").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "failed",
                    "retcode": 200,
                    "data": None,
                    "message": "消息已过期或者为内层消息，无法获取转发消息",
                },
            ),
        )
        resolver = ForwardResolver("http://napcat")
        try:
            resolved = await resolver.resolve(
                "expired-forward",
                source_message_id=123,
                source_ctx_id=4,
            )
        finally:
            await resolver.close()

    assert resolved is None
    assert not await ForwardRecord.filter(forward_id="expired-forward").exists()
