"""Per-tool-call progress channel bound by the harness."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from attrs import define

if TYPE_CHECKING:
    from ..runtime.tasks import EmitFn

_current_progress: ContextVar[ToolProgress | None] = ContextVar("current_progress", default=None)


def current_progress() -> ToolProgress | None:
    return _current_progress.get()


def bind_progress(
    *,
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
    _emit: EmitFn
    _conversation_id: str
    _tool_call_id: str
    _tool_name: str

    def write(self, text: str) -> None:
        if not text:
            return
        self._emit(
            "conversation.tool_progress",
            conversation_id=self._conversation_id,
            tool_call_id=self._tool_call_id,
            tool_name=self._tool_name,
            text=text,
        )

    def set_task(self, label: str) -> None:
        self._emit(
            "conversation.tool_progress",
            conversation_id=self._conversation_id,
            tool_call_id=self._tool_call_id,
            tool_name=self._tool_name,
            task=label,
        )
