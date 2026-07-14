"""Per-tool-call progress channel bound by the harness."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from attrs import define, field

from ..domain.stream import StreamEvent, ToolResultDeltaPayload
from ..runtime.event_payloads import ConversationStreamPayload, ConversationToolProgressPayload
from ..runtime.pty_display import PtyDisplayBuffer, filter_tool_output
from ..util.secrets import redact_value

if TYPE_CHECKING:
    from ..runtime.event_payloads import EmitFn

_current_progress: ContextVar[ToolProgress | None] = ContextVar("current_progress", default=None)
MAX_PROGRESS_OUTPUT_BYTES = 1024 * 1024


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
    _display: PtyDisplayBuffer = field(factory=PtyDisplayBuffer, init=False)
    _snapshot: str = field(default="", init=False)

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id

    @property
    def tool_name(self) -> str:
        return self._tool_name

    def write(self, text: str) -> None:
        """Feed raw terminal output and publish the current display snapshot."""
        if not text:
            return
        self._display.feed(text)
        self._publish_snapshot(self._display.snapshot())

    def replace(self, text: str) -> None:
        """Replace the terminal display from an already-rendered snapshot."""
        display = PtyDisplayBuffer()
        display.feed(text)
        self._display = display
        self._publish_snapshot(display.snapshot())

    def snapshot(self) -> str:
        return self._snapshot

    def _publish_snapshot(self, snapshot: str) -> None:
        filtered = filter_tool_output(snapshot)
        redacted = redact_value(filtered)
        visible = redacted if isinstance(redacted, str) else filtered
        encoded = visible.encode()
        if len(encoded) > MAX_PROGRESS_OUTPUT_BYTES:
            visible = encoded[:MAX_PROGRESS_OUTPUT_BYTES].decode("utf-8", errors="replace")
            visible += f"\n[system] output truncated at {MAX_PROGRESS_OUTPUT_BYTES} bytes"
        if visible == self._snapshot:
            return
        self._snapshot = visible
        self._emit(
            ConversationStreamPayload(
                self._conversation_id,
                StreamEvent(
                    self._tool_call_id,
                    "tool_result_delta",
                    ToolResultDeltaPayload(
                        self._tool_call_id,
                        self._tool_name,
                        visible,
                    ),
                ),
            )
        )
        self._emit(
            ConversationToolProgressPayload(
                self._conversation_id,
                self._tool_call_id,
                self._tool_name,
                visible,
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
