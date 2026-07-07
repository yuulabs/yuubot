"""WebSocket command handlers.

Ordering contract: the ack/result frame of a command is always sent before any
push frame it produces. Busy detection is owned by the Conversation itself
(``ConversationBusy``); the facade only translates it to an error frame, so
there is no connection-level bookkeeping to race with.
"""

import asyncio

import msgspec

from ..app import Yuubot
from ..runtime.tasks import TaskNotRunningError
from ..chat import ConversationBlocked, ConversationBusy
from ..domain.messages import ContentItem, InputMessage
from ..chat.listener import WsListener
from .errors import internal_error_detail, internal_error_message
from .types import WSCommandSend


async def handle_ws_command(
    app: Yuubot,
    raw: str,
    send: WSCommandSend,
    ws_listener: WsListener,
) -> asyncio.Task[None] | None:
    try:
        command = msgspec.json.decode(raw.encode(), type=dict[str, object])
        command_type = _required_str(command, "type")
        command_id = command.get("id")
        payload = _payload(command)
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
        await send_error(send, None, "bad_request", str(exc))
        return None
    if command_type == "conversation.send":
        return await _start_conversation_send(app, send, ws_listener, command_id, payload)
    if command_type == "runtime.events.subscribe":
        return asyncio.create_task(_runtime_events_subscribe(send, ws_listener, command_id, payload))
    if command_type == "conversation.history.subscribe":
        return asyncio.create_task(_history_subscribe(send, ws_listener, command_id, payload))
    if command_type == "task.subscribe":
        return asyncio.create_task(_task_subscribe(app, send, ws_listener, command_id, payload))
    if command_type == "task.stdin":
        return asyncio.create_task(_task_stdin(app, send, command_id, payload))
    if command_type == "conversation.interrupt":
        conversation_id = _optional_str(payload.get("conversation_id"))
        if not conversation_id:
            await send_error(send, command_id, "bad_request", "conversation_id is required")
            return None
        await send(
            {
                "id": command_id,
                "type": "conversation.interrupt.result",
                "payload": {"conversation_id": conversation_id, "interrupted": app.interrupt(conversation_id)},
            }
        )
        return None
    if command_type == "task.cancel":
        task_id = _optional_str(payload.get("task_id"))
        if not task_id:
            await send_error(send, command_id, "bad_request", "task_id is required")
            return None
        if task_id not in app.runtime.tasks:
            await send_error(send, command_id, "not_found", "task not found")
            return None
        app.runtime.cancel_runtime_task(task_id)
        await send({"id": command_id, "type": "task.cancel.result", "payload": app.task_snapshot(task_id)})
        return None
    await send_error(send, command_id, "bad_request", f"unknown command type: {command_type}")
    return None


async def _start_conversation_send(
    app: Yuubot,
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: object,
    payload: dict[str, object],
) -> asyncio.Task[None] | None:
    actor_id = _optional_str(payload.get("actor_id"))
    if not actor_id:
        await send_error(send, command_id, "bad_request", "actor_id is required")
        return None
    actor = app.actors.get(actor_id)
    if actor is None:
        await send_error(send, command_id, "not_found", "actor not found")
        return None
    try:
        content = _input_content(payload)
    except (ValueError, msgspec.ValidationError) as exc:
        await send_error(send, command_id, "bad_request", str(exc))
        return None

    conversation = await app.runtime.conversations.get_or_create(actor, _optional_str(payload.get("conversation_id")))
    if conversation.running:
        await send_error(send, command_id, "conversation_busy", "conversation is already running")
        return None
    await send({"id": command_id, "type": "conversation.send.accepted", "payload": {"conversation_id": conversation.id}})
    ws_listener.track_send(command_id, conversation.id)
    message = InputMessage(role="user", name=actor_id, content=content)
    return asyncio.create_task(
        _conversation_send(send, command_id, app, actor_id, conversation.id, message, development=app.runtime.development),
        name="conversation_send",
    )


async def _conversation_send(
    send: WSCommandSend,
    command_id: object,
    app: Yuubot,
    actor_id: str,
    conversation_id: str,
    message: InputMessage,
    *,
    development: bool,
) -> None:
    try:
        await app.run_user_message(actor_id, message, conversation_id)
    except ConversationBusy:
        await send_error(send, command_id, "conversation_busy", "conversation is already running")
    except ConversationBlocked as exc:
        await send_error(send, command_id, "conversation_blocked", "conversation blocked", detail={"reason": str(exc)})
    except Exception as exc:
        await send_error(
            send,
            command_id,
            "internal_error",
            internal_error_message(exc, development=development),
            detail=internal_error_detail(exc, development=development),
        )


async def _runtime_events_subscribe(
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: object,
    payload: dict[str, object],
) -> None:
    try:
        kinds = _string_set(payload.get("kinds"))
    except ValueError as exc:
        await send_error(send, command_id, "bad_request", str(exc))
        return
    ws_listener.track_events(kinds)
    await send({"id": command_id, "type": "runtime.events.subscribe.result", "payload": {"kinds": sorted(kinds)}})


async def _history_subscribe(
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: object,
    payload: dict[str, object],
) -> None:
    conversation_id = _optional_str(payload.get("conversation_id"))
    if not conversation_id:
        await send_error(send, command_id, "bad_request", "conversation_id is required")
        return
    ws_listener.track_history(conversation_id)
    await send(
        {
            "id": command_id,
            "type": "conversation.history.subscribe.result",
            "payload": {"conversation_id": conversation_id},
        }
    )


async def _task_subscribe(
    app: Yuubot,
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: object,
    payload: dict[str, object],
) -> None:
    task_id = _optional_str(payload.get("task_id"))
    if not task_id:
        await send_error(send, command_id, "bad_request", "task_id is required")
        return
    if task_id not in app.runtime.tasks:
        await send_error(send, command_id, "not_found", "task not found")
        return
    task = app.runtime.tasks.get(task_id)
    await send({"id": command_id, "type": "task.subscribe.result", "payload": {"task_id": task_id}})
    await send({
        "type": "task.event",
        "payload": {"task_id": task_id, "status": task.status, "stdout": task.stdout.tail(max_bytes=65536)},
    })
    ws_listener.start_task_stdout(task_id, task.stdout, task.status)
    try:
        try:
            await task.wait_terminal()
        except asyncio.CancelledError:
            pass
        await ws_listener.send_task_terminal(task_id, task.status)
    finally:
        ws_listener.stop_task_stdout()


async def _task_stdin(
    app: Yuubot,
    send: WSCommandSend,
    command_id: object,
    payload: dict[str, object],
) -> None:
    task_id = _optional_str(payload.get("task_id"))
    text = payload.get("text")
    if not task_id:
        await send_error(send, command_id, "bad_request", "task_id is required")
        return
    if not isinstance(text, str):
        await send_error(send, command_id, "bad_request", "text is required")
        return
    if task_id not in app.runtime.tasks:
        await send_error(send, command_id, "not_found", "task not found")
        return
    try:
        snapshot = app.task_stdin_write(task_id, text)
    except TaskNotRunningError as exc:
        await send_error(send, command_id, "conflict", str(exc))
        return
    await send({"id": command_id, "type": "task.stdin.result", "payload": msgspec.to_builtins(snapshot)})


async def send_error(
    send: WSCommandSend,
    command_id: object,
    code: str,
    message: str,
    detail: dict[str, object] | None = None,
) -> None:
    error: dict[str, object] = {"code": code, "message": message}
    if detail:
        error["detail"] = detail
    await send({"id": command_id, "type": "error", "error": error})


def _payload(command: dict[str, object]) -> dict[str, object]:
    payload = command.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return payload


def _required_str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} is required")
    return item


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_set(value: object) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("kinds must be a list of strings")
    return set(value)


def _input_content(payload: dict[str, object]) -> list[ContentItem]:
    content = payload.get("content")
    if content is None:
        raise ValueError("content is required")
    items = msgspec.convert(content, list[ContentItem])
    if not items:
        raise ValueError("at least one content item is required")
    return items
