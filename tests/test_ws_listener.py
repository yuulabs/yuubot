import pytest

from yuubot.chat.listener import WsListener


@pytest.mark.asyncio
async def test_track_send_replaces_existing_conversation_track() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_send("cmd1", "conv-1")
    listener.track_send("cmd2", "conv-1")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "hi"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert stream_frames[0]["id"] == "cmd2"


@pytest.mark.asyncio
async def test_track_send_keeps_different_conversations() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_send("cmd1", "conv-1")
    listener.track_send("cmd2", "conv-2")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "a"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert stream_frames[0]["id"] == "cmd1"


@pytest.mark.asyncio
async def test_history_subscriber_receives_stream_without_track_send() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_history("conv-1")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "hi"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert "id" not in stream_frames[0]


@pytest.mark.asyncio
async def test_track_send_prevents_duplicate_history_stream() -> None:
    sent: list[dict[str, object]] = []

    async def send(payload: dict[str, object]) -> None:
        sent.append(payload)

    listener = WsListener(send)
    listener.track_history("conv-1")
    listener.track_send("cmd1", "conv-1")

    await listener.on_event(
        "conversation.stream",
        {
            "conversation_id": "conv-1",
            "event": {"kind": "text_delta", "group_id": "text-0", "payload": {"text": "hi"}},
        },
    )

    stream_frames = [frame for frame in sent if frame.get("type") == "conversation.stream"]
    assert len(stream_frames) == 1
    assert stream_frames[0]["id"] == "cmd1"
