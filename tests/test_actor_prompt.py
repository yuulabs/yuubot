from __future__ import annotations

from pathlib import Path

from yuubot.actor.prompt import (
    REAL_TIME_CONTEXT_MARKER,
    augment_user_message,
    developer_prompt,
    user_visible_text,
)
from yuubot.domain.messages import InputMessage, text_content


def test_developer_prompt_documents_cron_facade(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "yb.tasks.cron:\n" in prompt
    assert "await yb.tasks.cron.add" in prompt
    assert "actor_message" in prompt
    assert "conversation_callback" in prompt
    assert "+1m" in prompt


def test_developer_prompt_real_time_data_is_static(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=False)
    real_time = prompt.split("# Real-Time Data\n", 1)[1]

    assert "platform: local" in real_time
    assert "timezone:" in real_time
    assert "## Session modes" in real_time
    assert "Conversation (User):" in real_time
    assert "Actor:" in real_time
    assert "Per-turn `mode` and `now`" in real_time
    assert "\nnow:" not in real_time
    assert "\nmode: conversation" not in real_time
    assert "\nmode: actor" not in real_time


def test_augment_user_message_round_trip() -> None:
    message = InputMessage(role="user", name="amy", content=text_content("hello"))
    augmented = augment_user_message(message, mode="actor")

    assert augmented.content[0].text.startswith(REAL_TIME_CONTEXT_MARKER)
    assert "mode: actor" in augmented.content[0].text
    assert "now:" in augmented.content[0].text
    assert user_visible_text(augmented) == "hello"
