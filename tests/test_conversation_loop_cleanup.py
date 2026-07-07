from __future__ import annotations

import queue
from pathlib import Path

import pytest

from yuubot.actor import ActorConfig
from yuubot.app import Yuubot
from yuubot.chat import harness as harness_module
from yuubot.domain import InputMessage, ModelCard, text_content
from yuubot.llm import scripted_reply


@pytest.mark.asyncio
async def test_terminal_turn_marks_closed_when_harness_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def close_raises(self: harness_module.Harness) -> None:
        del self
        raise queue.Empty()

    monkeypatch.setattr(harness_module.Harness, "close", close_raises)

    app = await Yuubot.create(tmp_path / "data")
    conversation_id = "conv-cleanup-test"
    try:
        app.create_actor(
            ActorConfig(
                id="amy",
                name="Amy",
                workspace=str(tmp_path / "workspace"),
                model=ModelCard(selector="fake"),
            ),
            scripted_reply("done"),
        )
        await app.run_user_message(
            "amy",
            InputMessage(role="user", name="amy", content=text_content("hi")),
            conversation_id,
        )
        rows = await app.runtime.state.list_conversations()
        record = next(item for item in rows if item.id == conversation_id)
        assert record.status == "closed"
    finally:
        await app.shutdown()
