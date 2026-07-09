from __future__ import annotations

from pathlib import Path

from yuubot.actor.prompt import (
    REAL_TIME_CONTEXT_MARKER,
    augment_user_message,
    developer_prompt,
    user_visible_text,
)
from yuubot.actor.prompt_docs import ADMIN_PAGES_INTRO, ADMIN_PAGES_SUBMIT_FLOW
from yuubot.domain.messages import InputMessage, text_content


def test_developer_prompt_documents_cron_facade(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "yb.tasks.cron:\n" in prompt
    assert "await add" in prompt
    assert "actor_message" in prompt
    assert "conversation_callback" in prompt
    assert "+1m" in prompt


def test_developer_prompt_documents_interactive_tasks(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "task.write" in prompt
    assert "PTY" in prompt
    assert "yb.tasks.submit" in prompt


def test_developer_prompt_guides_single_execute_python_orchestration(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "Prefer one execute_python call" in prompt
    assert "execute_python calls are not concurrent" in prompt
    assert "inside one submitted code block" in prompt
    assert "Example execute_python code block:\n```python\n" in prompt
    assert "results = await yext.web.search(query)" in prompt
    assert "page = await yext.web.read(results[0].url)" in prompt
    assert "print(page[:2000])" in prompt
    assert "issues = await repo.issues.list_recent()" in prompt
    assert "print a small slice or summary first" in prompt


def test_developer_prompt_documents_workspace_ref_markers(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "[[ relative/path ]]" in prompt
    assert "use the read tool to inspect referenced files" in prompt
    assert "workspace images or files you created" in prompt


def test_developer_prompt_documents_task_retention(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "ttl_s <= 3600" in prompt
    assert "expiring offload buffer" in prompt
    assert "resumable workspace scripts" in prompt


def test_developer_prompt_documents_actor_id_for_kv_urls(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], actor_id="amy", has_python=True)

    assert "Actor id: amy" in prompt
    assert ADMIN_PAGES_INTRO in prompt
    assert ADMIN_PAGES_SUBMIT_FLOW in prompt
    assert "`{actor_id}` is your Actor id" in prompt
    assert "/api/actors/{actor_id}/kv/{key}" in prompt
    assert "PUT` body must be `JSON.stringify({ value: yourObjectOrArray })" in prompt
    assert "sending the raw state object returns `400 bad_request`" in prompt
    assert "(await res.json()).value" in prompt
    assert "body: JSON.stringify({ value: state })" in prompt


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
    message = InputMessage("user", "amy", text_content("hello"))
    augmented = augment_user_message(message, "actor")

    assert augmented.content[0].text.startswith(REAL_TIME_CONTEXT_MARKER)
    assert "mode: actor" in augmented.content[0].text
    assert "now:" in augmented.content[0].text
    assert user_visible_text(augmented) == "hello"
