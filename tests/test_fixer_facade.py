from __future__ import annotations

import pytest

import yb.fixer
from yb._turn_guard import configure


@pytest.mark.asyncio
async def test_fixer_facade_returns_pending_answer_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def request_json(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["timeout_s"] == 40
        return {"status": "pending", "task_id": "t-fixer"}

    configure("pending-fixer-test")
    monkeypatch.setattr(yb.fixer, "request_json", request_json)

    result = await yb.fixer.ask_gemini("slow question")

    assert isinstance(result, yb.fixer.PendingAnswer)
    assert result.task_id == "t-fixer"
    assert "do not retry or poll" in result.message


@pytest.mark.asyncio
async def test_fixer_facade_returns_completed_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "status": "done",
            "task_id": "t-fixer",
            "text": "answer",
            "citations": [{"url": "https://example.com", "title": "Source"}],
        }

    configure("completed-fixer-test")
    monkeypatch.setattr(yb.fixer, "request_json", request_json)

    result = await yb.fixer.ask_grok("question")

    assert isinstance(result, yb.fixer.Answer)
    assert result.text == "answer"
    assert result.citations[0].url == "https://example.com"
