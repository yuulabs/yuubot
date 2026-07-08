"""MCP OAuth browser callback coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING

from attrs import define, field

from ..util.time import utc_now_iso
from .auth_attempts import AuthAttempt
from .mcp import McpServerRecord, McpServerState, McpManager, replace_mcp_record, summarize_capabilities, is_oauth_auth_mode

if TYPE_CHECKING:
    from .store import ApplicationStateStore

UpdateAuthAttempt = Callable[..., Awaitable[AuthAttempt]]


@define
class McpOAuthCoordinator:
    _callbacks: dict[str, asyncio.Future[tuple[str, str | None]]] = field(factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(factory=dict)

    def begin(self, attempt_id: str) -> asyncio.Future[tuple[str, str | None]]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[str, str | None]] = loop.create_future()
        self._callbacks[attempt_id] = future
        return future

    def complete(self, attempt_id: str, *, code: str, state: str | None) -> asyncio.Future[tuple[str, str | None]]:
        future = self._callbacks[attempt_id]
        if not future.done():
            future.set_result((code, state))
        return future

    def start_task(self, attempt_id: str, coro: Coroutine[object, object, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self._tasks[attempt_id] = task
        task.add_done_callback(lambda _task, attempt_id=attempt_id: self._tasks.pop(attempt_id, None))
        return task

    def drop_callback(self, attempt_id: str) -> None:
        self._callbacks.pop(attempt_id, None)

    def cancel(self, attempt_id: str) -> None:
        task = self._tasks.pop(attempt_id, None)
        if task is not None:
            task.cancel()
        future = self._callbacks.pop(attempt_id, None)
        if future is not None and not future.done():
            future.cancel()

    def cancel_for_server(self, server_id: str, auth_attempts: Mapping[str, AuthAttempt]) -> None:
        prefix = f"mcp:{server_id}"
        for attempt_id, attempt in list(auth_attempts.items()):
            if attempt.connection_id != prefix:
                continue
            self.cancel(attempt_id)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        for future in list(self._callbacks.values()):
            if not future.done():
                future.cancel()
        self._callbacks.clear()


async def run_mcp_oauth_attempt(
    *,
    record: McpServerRecord,
    attempt_id: str,
    redirect_uri: str,
    future: asyncio.Future[tuple[str, str | None]],
    manager: McpManager,
    state: ApplicationStateStore,
    auth_attempts: dict[str, AuthAttempt],
    update_auth_attempt: UpdateAuthAttempt,
    coordinator: McpOAuthCoordinator,
) -> None:
    async def redirect_handler(authorization_url: str) -> None:
        await update_auth_attempt(
            attempt_id,
            status="waiting_for_user",
            action={
                "kind": "open_url",
                "server_id": record.id,
                "url": authorization_url,
                "callback_url": redirect_uri,
                "title": f"Authorize {record.name}",
            },
        )
        manager.states[record.id] = McpServerState(
            status="needs_auth",
            action_hint={
                "kind": "open_url",
                "server_id": record.id,
                "url": authorization_url,
                "callback_url": redirect_uri,
                "title": f"Authorize {record.name}",
            },
            last_checked_at=utc_now_iso(),
        )

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.wait_for(future, timeout=600)

    try:
        index = await manager.discover_with_oauth(
            record,
            redirect_uri=redirect_uri,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout_s=600,
        )
    except Exception as exc:
        if attempt_id in auth_attempts:
            await update_auth_attempt(attempt_id, status="failed", error=str(exc))
        manager.states[record.id] = McpServerState(
            status="needs_auth",
            last_error=str(exc),
            action_hint={"kind": "start_mcp_oauth", "server_id": record.id, "title": f"Authorize {record.name}"},
            last_checked_at=utc_now_iso(),
        )
        await state.put_mcp_server(record, enabled=record.enabled, last_error=str(exc))
        return
    finally:
        coordinator.drop_callback(attempt_id)
    manager.indexes[record.id] = index
    manager.states[record.id] = McpServerState(
        status="ready",
        capabilities_summary=summarize_capabilities(index),
        last_checked_at=utc_now_iso(),
    )
    await state.put_mcp_server(record, enabled=record.enabled, capabilities=index)
    if attempt_id in auth_attempts:
        await update_auth_attempt(attempt_id, status="succeeded")


def ensure_oauth_credential_id(record: McpServerRecord) -> McpServerRecord:
    if record.credential_id or not is_oauth_auth_mode(record.auth_mode):
        return record
    return replace_mcp_record(
        record,
        credential_id=f"mcp:{record.id}:oauth",
        updated_at=utc_now_iso(),
    )
