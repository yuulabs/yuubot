from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yuuagents.core.eventbus import RuntimeEvent
from yuuagents.obs.observability import YuuTraceObserver


def _make_event(
    name: str,
    agent_id: str = "agent-1",
    agent_name: str = "test-agent",
    data: dict[str, object] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        name=name,
        agent_id=agent_id,
        agent_name=agent_name,
        timestamp=0.0,
        data=data or {},
    )


class TestWhitelistNewEvents:
    """Verify newly added event names are recorded via yuutrace.add_event."""

    @pytest.mark.asyncio
    async def test_tool_result_appended_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event(
            "tool.result_appended",
            data={
                "tool_call_id": "call_abc",
                "tool_name": "echo.echo",
                "result": "ok",
                "status": "completed",
            },
        )
        await observer.on_event(event)
        mock_add_event.assert_called_once()
        assert mock_add_event.call_args[0][0] == "tool.result_appended"

    @pytest.mark.asyncio
    async def test_task_failed_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event(
            "runtime.task_failed",
            data={"task_id": "task_1", "tool_name": "test.tool"},
        )
        await observer.on_event(event)
        mock_add_event.assert_called_once()
        assert mock_add_event.call_args[0][0] == "runtime.task_failed"

    @pytest.mark.asyncio
    async def test_task_timed_out_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event(
            "runtime.task_timed_out",
            data={"task_id": "task_2", "tool_name": "test.tool"},
        )
        await observer.on_event(event)
        mock_add_event.assert_called_once()
        assert mock_add_event.call_args[0][0] == "runtime.task_timed_out"


class TestWhitelistExistingEntriesUnaffected:
    """Verify existing whitelist entries still trigger recording."""

    @pytest.mark.asyncio
    async def test_llm_started_still_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event("llm.started")
        await observer.on_event(event)
        mock_add_event.assert_called_once()
        assert mock_add_event.call_args[0][0] == "llm.started"

    @pytest.mark.asyncio
    async def test_task_created_still_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event(
            "runtime.task_created",
            data={"task_id": "task_3", "tool_name": "test.tool"},
        )
        await observer.on_event(event)
        mock_add_event.assert_called_once()
        assert mock_add_event.call_args[0][0] == "runtime.task_created"

    @pytest.mark.asyncio
    async def test_budget_exceeded_still_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event("budget.exceeded")
        await observer.on_event(event)
        mock_add_event.assert_called_once()
        assert mock_add_event.call_args[0][0] == "budget.exceeded"


class TestNonWhitelistNotRecorded:
    """Verify events outside the whitelist do NOT trigger yuutrace.add_event."""

    @pytest.mark.asyncio
    async def test_agent_started_not_recorded_by_whitelist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """agent.started is handled by a dedicated branch, not the whitelist."""
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event(
            "agent.started",
            data={"agent_id": "agent-1", "agent_name": "test-agent"},
        )
        await observer.on_event(event)
        mock_add_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_finished_dedicated_handler_not_via_whitelist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm.finished has its own handler; with usage data it skips _record_runtime_event."""
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        # Provide usage data so _on_llm_finished takes the turn usage path,
        # not the fallback _record_runtime_event path.
        observer = YuuTraceObserver()
        event = _make_event(
            "llm.finished",
            data={
                "usage": {
                    "provider": "test",
                    "model": "test-model",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 15,
                },
            },
        )
        await observer.on_event(event)
        # With usage data present, _on_llm_finished tries to record usage
        # via turn.usage() / yuutrace.record_llm_usage(), not add_event.
        mock_add_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_event_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Completely unknown event names should not trigger recording."""
        from yuuagents.obs import observability

        mock_add_event = MagicMock()
        monkeypatch.setattr(observability.yuutrace, "add_event", mock_add_event)

        observer = YuuTraceObserver()
        event = _make_event("some.unknown.event")
        await observer.on_event(event)
        mock_add_event.assert_not_called()
