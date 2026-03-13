import json

from yuubot.recorder.server import _log_raw_event


def test_log_raw_event_emits_json(monkeypatch):
    captured: list[str] = []

    monkeypatch.setattr(
        "yuubot.recorder.server.logger.debug",
        lambda message, payload: captured.append(payload),
    )

    _log_raw_event({"post_type": "notice", "notice_type": "group_recall", "message_id": 123})

    assert captured
    raw = json.loads(captured[0])
    assert raw["post_type"] == "notice"
    assert raw["notice_type"] == "group_recall"
