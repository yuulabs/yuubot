"""Per-tool-call progress channel bound by the harness."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from attrs import define, field

from ..domain.stream import StreamEvent, ToolResultDeltaPayload
from ..runtime.event_payloads import ConversationStreamPayload, ConversationToolProgressPayload

if TYPE_CHECKING:
    from ..runtime.event_payloads import EmitFn

_current_progress: ContextVar[ToolProgress | None] = ContextVar("current_progress", default=None)


def current_progress() -> ToolProgress | None:
    return _current_progress.get()


def bind_progress(
    emit: EmitFn,
    conversation_id: str,
    tool_call_id: str,
    tool_name: str,
) -> ToolProgress:
    return ToolProgress(
        emit=emit,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )


@define
class ToolProgress:
    _emit: EmitFn = field(alias="emit")
    _conversation_id: str = field(alias="conversation_id")
    _tool_call_id: str = field(alias="tool_call_id")
    _tool_name: str = field(alias="tool_name")

    def write(self, text: str) -> None:
        if not text:
            return
        self._emit(
            ConversationStreamPayload(
                self._conversation_id,
                StreamEvent(
                    self._tool_call_id,
                    "tool_result_delta",
                    ToolResultDeltaPayload(
                        self._tool_call_id,
                        self._tool_name,
                        text,
                    ),
                ),
            )
        )
        self._emit(
            ConversationToolProgressPayload(
                self._conversation_id,
                self._tool_call_id,
                self._tool_name,
                text,
            )
        )

    def set_task(self, label: str) -> None:
        self._emit(
            ConversationToolProgressPayload(
                self._conversation_id,
                self._tool_call_id,
                self._tool_name,
                task=label,
            )
        )
