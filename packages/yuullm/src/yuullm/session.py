"""YuuSession -- stateful chat session with recovery-aware provider fallback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from .pool import ProviderPool
from .types import (
    AttemptRecovery,
    CallRecord,
    History,
    Message,
    MessageContent,
    ModelBinding,
    Reasoning,
    RedactedThinkingItem,
    Response,
    Store,
    StreamCursor,
    StreamItem,
    ThinkingBlock,
    ThinkingItem,
    Tick,
    ToolCall,
    ToolCallItem,
    ToolResultItem,
    StreamResult,
    is_text_item,
    is_tool_call_item,
)


class YuuSession:
    """Stateful chat session with history management and provider fallback."""

    def __init__(
        self,
        pool: ProviderPool,
        selector: str,
        history: History | None = None,
    ) -> None:
        self._pool = pool
        self._selector = selector
        self._history: History = list(history) if history else []
        self._streaming = False

    # -- History management --------------------------------------------------

    def append(self, msg: Message) -> None:
        """Validate *msg* and append it to the session history."""
        if self._streaming:
            raise RuntimeError(
                "cannot append to session history while stream is active"
            )

        if msg.role == "user":
            self._validate_user_message()

        if msg.role == "tool":
            self._validate_tool_message(msg)

        self._history.append(msg)

    @property
    def history(self) -> History:
        """Return the current conversation history.

        The returned list is the internal history.  Callers are expected to use
        :meth:`append` for normal mutation so session invariants are preserved.
        """
        return self._history

    def _validate_user_message(self) -> None:
        if not self._history:
            return
        last = self._history[-1]
        if isinstance(last, Message) and last.role == "user":
            raise ValueError("consecutive user messages are not allowed")
        if _pending_tool_call_ids(self._history):
            raise ValueError("user message cannot be appended before tool results")

    def _validate_tool_message(self, msg: Message) -> None:
        pending = _pending_tool_call_ids(self._history)
        if not pending:
            if not self._history:
                raise ValueError(
                    "tool message requires a prior assistant message with open tool calls"
                )
            raise ValueError(
                "tool message must follow an assistant message with open tool calls"
            )

        result_ids = _tool_result_ids(msg)
        if not result_ids:
            raise ValueError("tool message must contain at least one tool_result item")

        seen: set[str] = set()
        for result_id in result_ids:
            if result_id in seen:
                raise ValueError(f"duplicate tool result for {result_id!r}")
            seen.add(result_id)
            if result_id not in pending:
                raise ValueError(
                    f"tool result {result_id!r} does not match an open tool call"
                )

    # -- Streaming -----------------------------------------------------------

    async def stream(self, **overrides: Any) -> StreamResult:
        """Stream a completion through the resolved provider with fallback.

        Recovery events are yielded as :class:`AttemptRecovery` stream items
        and also stored in ``store.recoveries``.
        """
        if "model" in overrides:
            raise ValueError("YuuSession.stream() does not accept model override")

        store = Store()
        return self._iterate(store, overrides), store

    async def _iterate(
        self,
        session_store: Store,
        overrides: dict[str, Any],
    ) -> AsyncIterator[StreamItem]:
        if self._streaming:
            raise RuntimeError("session stream is already active")
        if _pending_tool_call_ids(self._history):
            raise ValueError("cannot stream while tool results are pending")

        self._streaming = True
        stream_seq = 0
        try:
            bindings = await self._pool.resolve(
                self._selector,
            )

            last_error: BaseException | None = None

            for attempt_index, binding in enumerate(bindings):
                client = self._pool.get_client(binding)
                history_for_call: History = list(self._history)
                pending_content: MessageContent = []
                # Per-attempt thinking buffer: Reasoning chunks accumulate here
                # and are finalized into a thinking item when ThinkingBlock
                # arrives.  A fresh buffer per attempt means a failed attempt's
                # partial thinking is discarded on recovery.
                thinking_buffer: list[str] = []
                attempt_start_seq = stream_seq
                started_at = time.monotonic()

                try:
                    iterator, provider_store = await client.stream(
                        history_for_call,
                        model=binding.model,
                        **overrides,
                    )

                    async for item in iterator:
                        yield item
                        stream_seq += 1
                        _accumulate_stream_item(
                            pending_content, item, thinking_buffer
                        )

                    if pending_content:
                        self._history.append(
                            Message(role="assistant", content=pending_content)
                        )

                    _copy_store(provider_store, session_store)
                    self._pool.record(
                        CallRecord(
                            provider_name=binding.provider_name,
                            model=binding.model,
                            selector=self._selector,
                            started_at=started_at,
                            finished_at=time.monotonic(),
                            usage=provider_store.usage,
                        )
                    )
                    return

                except asyncio.CancelledError:
                    # Mid-stream cancellation (e.g. user clicked Stop):
                    # append whatever content was accumulated so the session
                    # history stays legal. The agent loop will synthesize
                    # tool results for any tool calls in the partial message.
                    if pending_content:
                        # Mirror opencode's cleanup principle: the partial
                        # assistant must be a legal "assistant turn" for the
                        # provider's request format. The OpenAI-compatible
                        # converter routes thinking items to
                        # ``reasoning_content`` and produces an entry with no
                        # ``content`` and no ``tool_calls`` when
                        # pending_content has ONLY thinking/redacted_thinking
                        # items — providers (DeepSeek notably) reject this as
                        # "consecutive user messages". Patch: if no text or
                        # tool_call item is present, append an empty text
                        # placeholder so the entry has at least one content
                        # block.
                        has_text_or_tool = any(
                            is_text_item(item) or is_tool_call_item(item)
                            for item in pending_content
                        )
                        if not has_text_or_tool:
                            pending_content = pending_content + [
                                {"type": "text", "text": ""}
                            ]
                        self._history.append(
                            Message(role="assistant", content=pending_content)
                        )
                    raise

                except (ConnectionError, TimeoutError, OSError) as exc:
                    last_error = exc
                    recovery = self._build_recovery(
                        failed=binding,
                        next_binding=_next_binding(bindings, attempt_index),
                        rollback_to=StreamCursor(
                            history_len=len(self._history),
                            stream_seq=attempt_start_seq,
                        ),
                        reason=str(exc),
                    )
                    session_store.recoveries.append(recovery)
                    yield recovery
                    stream_seq += 1
                    self._pool.invalidate(self._selector, binding.provider_name)
                    continue

                except Exception as exc:
                    status = getattr(exc, "status_code", None) or getattr(
                        exc, "status", None
                    )
                    if status is not None and 500 <= status < 600:
                        last_error = exc
                        recovery = self._build_recovery(
                            failed=binding,
                            next_binding=_next_binding(bindings, attempt_index),
                            rollback_to=StreamCursor(
                                history_len=len(self._history),
                                stream_seq=attempt_start_seq,
                            ),
                            reason=str(exc),
                        )
                        session_store.recoveries.append(recovery)
                        yield recovery
                        stream_seq += 1
                        self._pool.invalidate(self._selector, binding.provider_name)
                        continue
                    raise

            raise RuntimeError(
                f"all providers exhausted for selector {self._selector!r}"
            ) from last_error
        finally:
            self._streaming = False

    def _build_recovery(
        self,
        *,
        failed: ModelBinding,
        next_binding: ModelBinding | None,
        rollback_to: StreamCursor,
        reason: str,
    ) -> AttemptRecovery:
        continuation = "non_seamless"
        if next_binding is not None and self._pool.supports_seamless_recovery(
            next_binding.provider_name
        ):
            continuation = "seamless"
        return AttemptRecovery(
            failed_provider=failed.provider_name,
            failed_model=failed.model,
            next_provider=next_binding.provider_name if next_binding else None,
            next_model=next_binding.model if next_binding else None,
            rollback_to=rollback_to,
            continuation=continuation,
            reason=reason,
        )


def _next_binding(
    bindings: list[ModelBinding],
    attempt_index: int,
) -> ModelBinding | None:
    next_index = attempt_index + 1
    if next_index >= len(bindings):
        return None
    return bindings[next_index]


def _accumulate_stream_item(
    pending_content: MessageContent,
    item: StreamItem,
    thinking_buffer: list[str],
) -> None:
    """Fold a streamed item into the assistant message being built.

    ``thinking_buffer`` accumulates ``Reasoning`` text fragments and is
    finalized when a ``ThinkingBlock`` arrives (which contributes only
    metadata -- ``signature`` / ``redacted_data`` -- not the text).  If no
    ``ThinkingBlock`` ever arrives the buffer is discarded, keeping
    ``Reasoning`` transient.  Consecutive ``Response`` text items are merged
    into a single content entry so the final message is not fragmented into
    one item per token.
    """
    match item:
        case Reasoning(item=inner):
            # Accumulate thinking text; finalized by ThinkingBlock.
            if isinstance(inner, dict) and inner.get("type") == "text":
                thinking_buffer.append(inner["text"])

        case Response(item=inner):
            # Merge consecutive text items into one block.  Providers yield one
            # Response per streaming token; without merging the final message
            # would contain one item per token.
            if isinstance(inner, dict) and inner.get("type") == "text":
                if pending_content:
                    last = pending_content[-1]
                    if (
                        isinstance(last, dict)
                        and last.get("type") == "text"
                    ):
                        # Create a new dict rather than mutating in place: the
                        # original may still be referenced by the yielded
                        # Response item.
                        pending_content[-1] = {
                            **last,
                            "text": last["text"] + inner["text"],
                        }
                        return
            pending_content.append(inner)

        case ToolCall() as tc:
            tool_call: ToolCallItem = {
                "type": "tool_call",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }
            pending_content.append(tool_call)

        case ThinkingBlock() as tb:
            # Finalize thinking from the buffer (source of truth) plus the
            # block's metadata.  Fallback to ``tb.thinking`` covers the edge
            # case of a ThinkingBlock without preceding Reasoning chunks.
            if tb.redacted_data is not None:
                thinking_item: ThinkingItem | RedactedThinkingItem = {
                    "type": "redacted_thinking",
                    "data": tb.redacted_data,
                }
            else:
                thinking_text = (
                    "".join(thinking_buffer) if thinking_buffer else tb.thinking
                )
                thinking_item = {"type": "thinking", "thinking": thinking_text}
                if tb.signature is not None:
                    thinking_item["signature"] = tb.signature

            # Insert before the first non-thinking item so thinking stays
            # before text/tool content even when the ThinkingBlock is emitted
            # at the end of the stream (OpenAI/DeepSeek order).
            insert_at = 0
            for i, existing in enumerate(pending_content):
                if isinstance(existing, dict) and existing.get("type") not in (
                    "thinking",
                    "redacted_thinking",
                ):
                    insert_at = i
                    break
            else:
                insert_at = len(pending_content)
            pending_content.insert(insert_at, thinking_item)
            thinking_buffer.clear()

        case Tick() | AttemptRecovery():
            pass


def _copy_store(source: Store, target: Store) -> None:
    target.usage = source.usage
    target.cost = source.cost
    target.provider_cost = source.provider_cost


def _tool_call_ids(msg: Message) -> list[str]:
    ids: list[str] = []
    for item in msg.content:
        if item["type"] == "tool_call":
            ids.append(_tool_call_id(item))
    return ids


def _tool_result_ids(msg: Message) -> list[str]:
    ids: list[str] = []
    for item in msg.content:
        if item["type"] == "tool_result":
            ids.append(_tool_result_id(item))
    return ids


def _pending_tool_call_ids(history: History) -> set[str]:
    pending: set[str] = set()
    for item in history:
        if not isinstance(item, Message):
            continue
        if item.role == "assistant":
            for tool_call_id in _tool_call_ids(item):
                if tool_call_id in pending:
                    raise ValueError(f"duplicate open tool call id {tool_call_id!r}")
                pending.add(tool_call_id)
        elif item.role == "tool":
            for tool_result_id in _tool_result_ids(item):
                if tool_result_id not in pending:
                    raise ValueError(
                        f"tool result {tool_result_id!r} has no open tool call"
                    )
                pending.remove(tool_result_id)
    return pending


def _tool_call_id(item: ToolCallItem) -> str:
    return item["id"]


def _tool_result_id(item: ToolResultItem) -> str:
    return item["tool_call_id"]
