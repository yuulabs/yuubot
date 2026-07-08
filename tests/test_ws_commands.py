import msgspec
import pytest

from yuubot.web.ws_commands import (
    ConversationHistorySubscribeCommand,
    ConversationInterruptCommand,
    ConversationSendCommand,
    RuntimeEventsSubscribeCommand,
    TaskCancelCommand,
    TaskStdinCommand,
    TaskSubscribeCommand,
    WSCommand,
)


def test_decode_conversation_send() -> None:
    command = msgspec.json.decode(
        b'{"type":"conversation.send","id":"cmd-1","payload":{"actor_id":"amy","content":[{"kind":"text","text":"hi"}]}}',
        type=WSCommand,
    )
    assert isinstance(command, ConversationSendCommand)
    assert command.id == "cmd-1"
    assert command.payload.actor_id == "amy"
    assert command.payload.content[0].text == "hi"


def test_decode_runtime_events_subscribe() -> None:
    command = msgspec.json.decode(
        b'{"type":"runtime.events.subscribe","payload":{"kinds":["notification.delivered"]}}',
        type=WSCommand,
    )
    assert isinstance(command, RuntimeEventsSubscribeCommand)
    assert command.payload.kinds == ["notification.delivered"]


def test_decode_conversation_history_subscribe() -> None:
    command = msgspec.json.decode(
        b'{"type":"conversation.history.subscribe","payload":{"conversation_id":"conv-1"}}',
        type=WSCommand,
    )
    assert isinstance(command, ConversationHistorySubscribeCommand)
    assert command.payload.conversation_id == "conv-1"


def test_decode_task_subscribe() -> None:
    command = msgspec.json.decode(
        b'{"type":"task.subscribe","payload":{"task_id":"task-1"}}',
        type=WSCommand,
    )
    assert isinstance(command, TaskSubscribeCommand)
    assert command.payload.task_id == "task-1"


def test_decode_task_stdin() -> None:
    command = msgspec.json.decode(
        b'{"type":"task.stdin","payload":{"task_id":"task-1","text":"ls\\n"}}',
        type=WSCommand,
    )
    assert isinstance(command, TaskStdinCommand)
    assert command.payload.text == "ls\n"


def test_decode_conversation_interrupt() -> None:
    command = msgspec.json.decode(
        b'{"type":"conversation.interrupt","payload":{"conversation_id":"conv-1"}}',
        type=WSCommand,
    )
    assert isinstance(command, ConversationInterruptCommand)


def test_decode_task_cancel() -> None:
    command = msgspec.json.decode(
        b'{"type":"task.cancel","payload":{"task_id":"task-1"}}',
        type=WSCommand,
    )
    assert isinstance(command, TaskCancelCommand)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"type":"task.subscribe","payload":{"task_id":""}}',
        b'{"type":"conversation.send","payload":{"actor_id":"","content":[{"kind":"text","text":"hi"}]}}',
        b'{"type":"task.stdin","payload":{"task_id":"task-1","text":""}}',
        b'{"type":"conversation.history.subscribe","payload":{"conversation_id":""}}',
    ],
)
def test_rejects_empty_required_strings(payload: bytes) -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(payload, type=WSCommand)


def test_rejects_unknown_command_type() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"type":"unknown.command","payload":{}}', type=WSCommand)
