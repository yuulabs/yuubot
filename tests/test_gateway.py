"""E2E-ish coverage for the channel gateway contract."""

import asyncio
import json

import httpx
import respx

from yuubot.channels.qq import QQRecorderAdapter
from yuubot.channels.web import WebChatAdapter
from yuubot.core.context import ContextManager
from yuubot.core.models import Context, TextSegment
from yuubot.daemon.gateway import (
    ChannelAdapter,
    ContextRef,
    Gateway,
    IncomingMessage,
    OutboundMessage,
    RoutingRule,
    RoutingEngine,
)
from yuubot.recorder.api import _send_via_pipeline


class DummyDispatcher:
    def __init__(self) -> None:
        self.messages = []

    async def dispatch_message(self, message) -> None:
        self.messages.append(message)


class DummyAdapter:
    channel = "discord"

    def __init__(self) -> None:
        self.sent = []

    async def start(self, emit) -> None:
        self.emit = emit

    async def stop(self) -> None:
        pass

    async def send(self, ctx: Context, message: OutboundMessage) -> None:
        self.sent.append((ctx.channel, ctx.key, ctx.metadata, message.text))


def _adapter_protocol(adapter: DummyAdapter) -> ChannelAdapter:
    return adapter


async def test_gateway_ingest_creates_context_and_dispatches_message(db) -> None:
    dispatcher = DummyDispatcher()
    gateway = Gateway(dispatcher=dispatcher)

    await gateway.ingest(
        IncomingMessage(
            context=ContextRef(
                channel="discord",
                key="guild:1/channel:2/thread:3",
                kind="thread",
                label="Guild / Thread",
                metadata={"channel_id": "2", "thread_id": "3", "channel_type": "thread"},
            ),
            message_id="m-1",
            sender_id="u-1",
            sender_name="Alice",
            segments=[TextSegment(text="hello")],
            text="hello",
            timestamp=123,
        )
    )

    ctx = await Context.get(channel="discord", key="guild:1/channel:2/thread:3")
    assert ctx.kind == "thread"
    assert ctx.metadata["thread_id"] == "3"

    [message] = dispatcher.messages
    assert message.ctx_id == ctx.id
    assert message.message_id == "m-1"
    assert message.metadata["sender_id"] == "u-1"


async def test_routing_uses_context_rules_then_kind_defaults(db) -> None:
    forum_ctx = await Context.create(
        channel="discord",
        key="guild:1/channel:2/thread:3",
        kind="thread",
        metadata={"channel_type": "thread"},
    )
    dm_ctx = await Context.create(channel="telegram", key="chat:7", kind="private")

    engine = RoutingEngine(
        rules=[
            RoutingRule(
                match={"channel": "discord", "kind": "thread", "metadata.channel_type": "thread"},
                actor="forum_bot",
            )
        ],
        defaults={"private": "shiori", "group": "yuu", "thread": "yuu", "other": "yuu"},
    )

    dispatcher = DummyDispatcher()
    gateway = Gateway(dispatcher=dispatcher)
    await gateway.ingest(
        IncomingMessage(
            context=ContextRef(channel="discord", key=forum_ctx.key, kind="thread"),
            message_id="m-2",
            sender_id="u-2",
            text="forum question",
        )
    )
    assert await engine.select_actor(dispatcher.messages[-1]) == "forum_bot"

    await gateway.ingest(
        IncomingMessage(
            context=ContextRef(channel="telegram", key=dm_ctx.key, kind="private"),
            message_id="m-3",
            sender_id="u-3",
            text="dm",
        )
    )
    assert await engine.select_actor(dispatcher.messages[-1]) == "shiori"


async def test_gateway_send_uses_context_channel_adapter(db) -> None:
    adapter = DummyAdapter()
    gateway = Gateway()
    gateway.register(_adapter_protocol(adapter))
    ctx = await Context.create(
        channel="discord",
        key="guild:1/channel:2",
        kind="group",
        metadata={"channel_id": "2"},
    )

    await gateway.send(ctx.id, OutboundMessage(text="pong"))

    assert adapter.sent == [("discord", "guild:1/channel:2", {"channel_id": "2"}, "pong")]


async def test_web_adapter_routes_replies_to_bound_session(db) -> None:
    adapter = WebChatAdapter()
    ctx = await Context.create(channel="web", key="session:admin", kind="session")
    queue = asyncio.Queue()
    adapter.bind_session("conv-1", queue)

    await adapter.send(ctx, OutboundMessage(text="hello", reply_to="conv-1"))

    payload = json.loads(await queue.get())
    assert payload == {"type": "message", "role": "assistant", "text": "hello"}


async def test_qq_adapter_sends_only_to_recorder_for_qq_context(db) -> None:
    adapter = QQRecorderAdapter("http://recorder.test")
    ctx = await Context.create(
        channel="qq",
        key="private:123",
        kind="private",
        metadata={"user_id": "123"},
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://recorder.test/send_msg_guaranteed").mock(
            return_value=httpx.Response(200, json={"status": "ok"}),
        )
        await adapter.send(ctx, OutboundMessage(text="pong"))

    body = json.loads(route.calls.last.request.content)
    assert body["message_type"] == "private"
    assert body["user_id"] == 123
    assert body["message"] == [{"type": "text", "data": {"text": "pong"}}]


async def test_recorder_pipeline_treats_onebot_business_failure_as_send_failure(db) -> None:
    class FailedResponse:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"status": "failed", "retcode": 200, "data": None, "message": "无法获取用户信息"}

    class FailedClient:
        async def post(self, path: str, json: dict) -> FailedResponse:
            assert path == "/send_msg"
            return FailedResponse()

    data, status = await _send_via_pipeline(
        client=FailedClient(),
        body={
            "message_type": "private",
            "user_id": 123,
            "message": [{"type": "text", "data": {"text": "hello"}}],
        },
        ctx_mgr=ContextManager(),
        bot_qq=456,
        bot_name="bot",
    )

    assert status == 502
    assert data["status"] == "failed"
