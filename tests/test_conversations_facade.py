from __future__ import annotations

import pytest

from yb import conversations


@pytest.mark.asyncio
async def test_list_recents_filters_actor_and_user_visible_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YUUBOT_ACTOR_ID", "amy")
    summaries = [
        {"id": "amy-1", "actor_id": "amy", "title": "Preferences"},
        {"id": "other-1", "actor_id": "bob", "title": "Private"},
    ]
    histories = {
        "amy-1": {
            "items": [
                {"kind": "system_prompt", "payload": {"text": "hidden"}},
                {
                    "kind": "input",
                    "payload": {
                        "role": "user",
                        "content": [{"kind": "text", "text": "I like tea"}, {"kind": "image"}],
                    },
                },
                {"kind": "gen_reasoning", "payload": {"text": "hidden thought"}},
                {"kind": "gen_tool_call", "payload": {"name": "secret"}},
                {"kind": "tool_result", "payload": {"content": []}},
                {"kind": "gen_text", "payload": {"text": "Noted."}},
                {"kind": "input", "payload": {"role": "developer", "content": [{"kind": "text", "text": "hidden"}]}},
            ]
        }
    }

    async def fake_value(*args: object, **kwargs: object) -> object:
        return summaries

    async def fake_json(method: str, url: str, *args: object, **kwargs: object) -> dict[str, object]:
        del method, args, kwargs
        return histories[url.rsplit("/", 2)[-2]]

    monkeypatch.setattr(conversations, "request_json_value", fake_value)
    monkeypatch.setattr(conversations, "request_json", fake_json)

    result = await conversations.list_recents()

    assert result == [
        conversations.Conversation(
            "amy-1",
            "Preferences",
            [
                conversations.ConversationMessage("user", "I like tea"),
                conversations.ConversationMessage("text", "Noted."),
            ],
        )
    ]
