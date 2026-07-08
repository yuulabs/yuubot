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
from ..domain.messages import InputMessage
from ..chat.listener import WsListener
from .errors import internal_error_detail, internal_error_message
from .types import WSCommandSend
from .ws_commands import (
    ConversationHistorySubscribeCommand,
    ConversationHistorySubscribePayload,
    ConversationInterruptCommand,
    ConversationSendCommand,
    ConversationSendPayload,
    RuntimeEventsSubscribeCommand,
    RuntimeEventsSubscribePayload,
    TaskCancelCommand,
    TaskStdinCommand,
    TaskStdinPayload,
    TaskSubscribeCommand,
    TaskSubscribePayload,
    WSCommand,
)


async def handle_ws_command(
    app: Yuubot,
    raw: str,
    send: WSCommandSend,
    ws_listener: WsListener,
) -> asyncio.Task[None] | None:
    try:
        command = msgspec.json.decode(raw.encode(), type=WSCommand)
    except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
        await send_error(send, None, "bad_request", str(exc))
        return None
    match command:
        case ConversationSendCommand(id=command_id, payload=payload):
            return await _start_conversation_send(app, send, ws_listener, command_id, payload)
        case RuntimeEventsSubscribeCommand(id=command_id, payload=payload):
            return asyncio.create_task(_runtime_events_subscribe(send, ws_listener, command_id, payload))
        case ConversationHistorySubscribeCommand(id=command_id, payload=payload):
            return asyncio.create_task(_history_subscribe(send, ws_listener, command_id, payload))
        case TaskSubscribeCommand(id=command_id, payload=payload):
            return asyncio.create_task(_task_subscribe(app, send, ws_listener, command_id, payload))
        case TaskStdinCommand(id=command_id, payload=payload):
            return asyncio.create_task(_task_stdin(app, send, command_id, payload))
        case ConversationInterruptCommand(id=command_id, payload=payload):
            conversation_id = payload.conversation_id
            await send(
                {
                    "id": command_id,
                    "type": "conversation.interrupt.result",
                    "payload": {"conversation_id": conversation_id, "interrupted": app.interrupt(conversation_id)},
                }
            )
            return None
        case TaskCancelCommand(id=command_id, payload=payload):
            task_id = payload.task_id
            if task_id not in app.runtime.tasks:
                await send_error(send, command_id, "not_found", "task not found")
                return None
            app.runtime.cancel_runtime_task(task_id)
            await send({"id": command_id, "type": "task.cancel.result", "payload": app.task_snapshot(task_id)})
            return None


async def _start_conversation_send(
    app: Yuubot,
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    payload: ConversationSendPayload,
) -> asyncio.Task[None] | None:
    actor_id = payload.actor_id
    actor = app.actors.get(actor_id)
    if actor is None:
        await send_error(send, command_id, "not_found", "actor not found")
        return None
    if not payload.content:
        await send_error(send, command_id, "bad_request", "at least one content item is required")
        return None

    conversation = await app.runtime.conversations.get_or_create(actor, payload.conversation_id)
    if conversation.running:
        await send_error(send, command_id, "conversation_busy", "conversation is already running")
        return None
    await send({"id": command_id, "type": "conversation.send.accepted", "payload": {"conversation_id": conversation.id}})
    ws_listener.track_send(command_id, conversation.id)
    message = InputMessage(role="user", name=actor_id, content=payload.content)
    return asyncio.create_task(
        _conversation_send(send, command_id, app, actor_id, conversation.id, message, development=app.runtime.development),
        name="conversation_send",
    )


async def _conversation_send(
    send: WSCommandSend,
    command_id: str | None,
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
    command_id: str | None,
    payload: RuntimeEventsSubscribePayload,
) -> None:
    kinds = set(payload.kinds)
    ws_listener.track_events(kinds)
    await send({"id": command_id, "type": "runtime.events.subscribe.result", "payload": {"kinds": sorted(kinds)}})


async def _history_subscribe(
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    payload: ConversationHistorySubscribePayload,
) -> None:
    conversation_id = payload.conversation_id
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
    command_id: str | None,
    payload: TaskSubscribePayload,
) -> None:
    task_id = payload.task_id
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
        await ws_listener.send_task_terminal(task_id, task.status, task.stdout.tail(max_bytes=65536))
    finally:
        ws_listener.stop_task_stdout()


async def _task_stdin(
    app: Yuubot,
    send: WSCommandSend,
    command_id: str | None,
    payload: TaskStdinPayload,
) -> None:
    task_id = payload.task_id
    if task_id not in app.runtime.tasks:
        await send_error(send, command_id, "not_found", "task not found")
        return
    try:
        snapshot = app.task_stdin_write(task_id, payload.text)
    except TaskNotRunningError as exc:
        await send_error(send, command_id, "conflict", str(exc))
        return
    await send({"id": command_id, "type": "task.stdin.result", "payload": msgspec.to_builtins(snapshot)})


async def send_error(
    send: WSCommandSend,
    command_id: str | None,
    code: str,
    message: str,
    detail: dict[str, object] | None = None,
) -> None:
    error: dict[str, object] = {"code": code, "message": message}
    if detail:
        error["detail"] = detail
    await send({"id": command_id, "type": "error", "error": error})
