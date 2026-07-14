"""WebSocket command handlers.

Ordering contract: the ack/result frame of a command is always sent before any
push frame it produces. Busy detection is owned by the Conversation itself
(``ConversationBusy``); the facade only translates it to an error frame, so
there is no connection-level bookkeeping to race with.
"""

import asyncio
import logging

import msgspec

from ..app import Yuubot
from ..runtime.tasks import TaskNotRunningError
from ..chat import (
    ConversationBlocked,
    ConversationBusy,
    Conversation,
    InvalidQuestionAnswers,
    QuestionNotPending,
)
from ..domain.messages import InputMessage, ToolResult
from ..chat.listener import WsListener
from .errors import internal_error_detail, internal_error_message, log_internal_error
from .types import WSCommandSend
from .ws_commands import (
    ConversationCloseCommand,
    ConversationAnswerCommand,
    ConversationAnswerPayload,
    ConversationOpenCommand,
    ConversationOpenPayload,
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
from .workspace_ref import normalize_conversation_content

_log = logging.getLogger(__name__)


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
            return await _start_conversation_send(
                app, send, ws_listener, command_id, payload
            )
        case ConversationAnswerCommand(id=command_id, payload=payload):
            return await _start_conversation_answer(
                app, send, ws_listener, command_id, payload
            )
        case RuntimeEventsSubscribeCommand(id=command_id, payload=payload):
            return asyncio.create_task(
                _runtime_events_subscribe(send, ws_listener, command_id, payload)
            )
        case ConversationOpenCommand(id=command_id, payload=payload):
            return asyncio.create_task(
                _conversation_open(app, send, ws_listener, command_id, payload)
            )
        case ConversationCloseCommand(id=command_id, payload=payload):
            ws_listener.close_conversation(payload.conversation_id)
            await send(
                {
                    "id": command_id,
                    "type": "conversation.close.result",
                    "payload": {"conversation_id": payload.conversation_id},
                }
            )
            return None
        case TaskSubscribeCommand(id=command_id, payload=payload):
            return asyncio.create_task(
                _task_subscribe(app, send, ws_listener, command_id, payload)
            )
        case TaskStdinCommand(id=command_id, payload=payload):
            return asyncio.create_task(_task_stdin(app, send, command_id, payload))
        case ConversationInterruptCommand(id=command_id, payload=payload):
            conversation_id = payload.conversation_id
            await send(
                {
                    "id": command_id,
                    "type": "conversation.interrupt.result",
                    "payload": {
                        "conversation_id": conversation_id,
                        "interrupted": app.interrupt(conversation_id),
                    },
                }
            )
            return None
        case TaskCancelCommand(id=command_id, payload=payload):
            task_id = payload.task_id
            if task_id not in app.runtime.tasks:
                await send_error(send, command_id, "not_found", "task not found")
                return None
            app.runtime.cancel_runtime_task(task_id)
            await send(
                {
                    "id": command_id,
                    "type": "task.cancel.result",
                    "payload": app.task_snapshot(task_id),
                }
            )
            return None
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
        await send_error(
            send, command_id, "bad_request", "at least one content item is required"
        )
        return None
    normalized_content = normalize_conversation_content(payload.content)
    if not normalized_content:
        await send_error(
            send, command_id, "bad_request", "at least one content item is required"
        )
        return None

    conversation = await app.runtime.conversations.get_or_create(
        actor, payload.conversation_id
    )
    if conversation.pending_question() is not None:
        await send_error(
            send,
            command_id,
            "conversation_awaiting_input",
            "conversation is waiting for an answer",
        )
        return None
    if conversation.running:
        _log.info(
            "conversation send rejected busy conversation_id=%s command_id=%s state=running",
            conversation.id,
            command_id,
        )
        await send_error(
            send, command_id, "conversation_busy", "conversation is already running"
        )
        return None
    await send(
        {
            "id": command_id,
            "type": "conversation.send.accepted",
            "payload": {"conversation_id": conversation.id},
        }
    )
    if not ws_listener.has_conversation(conversation.id):
        await ws_listener.open_conversation(conversation)
    message = InputMessage("user", actor_id, normalized_content)
    task = asyncio.create_task(
        _conversation_send(
            send,
            ws_listener,
            command_id,
            app,
            actor_id,
            conversation.id,
            message,
            app.runtime.development,
        ),
        name="conversation_send",
    )
    app.runtime.track_detached_task(task)
    task.add_done_callback(_log_background_task_result)
    return None


async def _start_conversation_answer(
    app: Yuubot,
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    payload: ConversationAnswerPayload,
) -> asyncio.Task[None] | None:
    row = await app.runtime.state.get_conversation(payload.conversation_id)
    if row is None:
        await send_error(send, command_id, "not_found", "conversation not found")
        return None
    actor = app.actors.get(row.actor_id)
    if actor is None:
        await send_error(send, command_id, "not_found", "conversation actor not found")
        return None
    conversation = await app.runtime.conversations.get_or_load(
        actor, payload.conversation_id
    )
    try:
        result = conversation.claim_question_answer(
            payload.tool_call_id, payload.answers, payload.skipped
        )
        try:
            await send(
                {
                    "id": command_id,
                    "type": "conversation.answer.accepted",
                    "payload": {
                        "conversation_id": conversation.id,
                        "tool_call_id": payload.tool_call_id,
                    },
                }
            )
        except BaseException:
            conversation.release_question_answer()
            raise
        if not ws_listener.has_conversation(conversation.id):
            await ws_listener.open_conversation(conversation)
        task = asyncio.create_task(
            _conversation_answer(
                send, ws_listener, command_id, conversation, payload, result
            ),
            name="conversation_answer",
        )
        app.runtime.track_detached_task(task)
        task.add_done_callback(_log_background_task_result)
    except QuestionNotPending:
        await send_error(
            send, command_id, "question_not_pending", "question is not pending"
        )
    except ConversationBusy:
        await send_error(
            send, command_id, "conversation_busy", "conversation is already running"
        )
    except InvalidQuestionAnswers as exc:
        await send_error(send, command_id, "invalid_question_answers", str(exc))
    return None


async def _conversation_answer(
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    conversation: Conversation,
    payload: ConversationAnswerPayload,
    result: ToolResult,
) -> None:
    try:
        await conversation.run_claimed_answer(result)
    except InvalidQuestionAnswers as exc:
        await _send_error_if_connected(
            send, command_id, "invalid_question_answers", str(exc)
        )
    except QuestionNotPending:
        await _send_error_if_connected(
            send, command_id, "question_not_pending", "question is not pending"
        )
    except ConversationBusy:
        _log.info(
            "conversation answer rejected busy conversation_id=%s command_id=%s state=raced",
            conversation.id,
            command_id,
        )
        await _send_error_if_connected(
            send, command_id, "conversation_busy", "conversation is already running"
        )
    except Exception as exc:
        await ws_listener.on_error(
            payload.conversation_id, internal_error_message(exc, False)
        )
        await _send_error_if_connected(
            send, command_id, "internal_error", internal_error_message(exc, False)
        )


async def _conversation_send(
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    app: Yuubot,
    actor_id: str,
    conversation_id: str,
    message: InputMessage,
    development: bool,
) -> None:
    try:
        await app.run_user_message(actor_id, message, conversation_id)
    except ConversationBusy:
        _log.info(
            "conversation send rejected busy conversation_id=%s command_id=%s state=raced",
            conversation_id,
            command_id,
        )
        await _send_error_if_connected(
            send, command_id, "conversation_busy", "conversation is already running"
        )
    except ConversationBlocked as exc:
        await _send_error_if_connected(
            send,
            command_id,
            "conversation_blocked",
            "conversation blocked",
            {"reason": str(exc)},
        )
    except Exception as exc:
        log_context = (
            f"conversation.send actor={actor_id} conversation={conversation_id}"
        )
        log_internal_error(_log, exc, log_context)
        await ws_listener.on_error(
            conversation_id, internal_error_message(exc, development)
        )
        await _send_error_if_connected(
            send,
            command_id,
            "internal_error",
            internal_error_message(exc, development),
            internal_error_detail(exc, development),
        )


async def _runtime_events_subscribe(
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    payload: RuntimeEventsSubscribePayload,
) -> None:
    kinds = set(payload.kinds)
    ws_listener.track_events(kinds)
    await send(
        {
            "id": command_id,
            "type": "runtime.events.subscribe.result",
            "payload": {"kinds": sorted(kinds)},
        }
    )


async def _conversation_open(
    app: Yuubot,
    send: WSCommandSend,
    ws_listener: WsListener,
    command_id: str | None,
    payload: ConversationOpenPayload,
) -> None:
    conversation_id = payload.conversation_id
    row = await app.runtime.state.get_conversation(conversation_id)
    if row is None:
        await send_error(send, command_id, "not_found", "conversation not found")
        return
    actor = app.actors.get(row.actor_id)
    if actor is None:
        await send_error(send, command_id, "not_found", "conversation actor not found")
        return
    conversation = await app.runtime.conversations.get_or_load(actor, conversation_id)
    await ws_listener.open_conversation(conversation)


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
    await send(
        {
            "id": command_id,
            "type": "task.subscribe.result",
            "payload": {"task_id": task_id},
        }
    )
    await send(
        {
            "type": "task.event",
            "payload": {
                "task_id": task_id,
                "status": task.status,
                "stdout": task.stdout.tail(max_bytes=1024 * 1024),
            },
        }
    )
    ws_listener.start_task_stdout(task_id, task.stdout, task.status)
    try:
        try:
            await task.wait_terminal()
        except asyncio.CancelledError:
            pass
        await ws_listener.send_task_terminal(
            task_id, task.status, task.stdout.tail(max_bytes=1024 * 1024)
        )
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
    await send(
        {
            "id": command_id,
            "type": "task.stdin.result",
            "payload": msgspec.to_builtins(snapshot),
        }
    )


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


async def _send_error_if_connected(
    send: WSCommandSend,
    command_id: str | None,
    code: str,
    message: str,
    detail: dict[str, object] | None = None,
) -> None:
    try:
        await send_error(send, command_id, code, message, detail)
    except Exception:
        _log.debug(
            "conversation.send error frame dropped command_id=%s code=%s",
            command_id,
            code,
            exc_info=True,
        )


def _log_background_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        _log.debug("detached conversation.send task cancelled")
    except Exception:
        _log.exception("detached conversation.send task failed")
