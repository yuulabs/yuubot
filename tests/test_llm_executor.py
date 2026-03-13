from yuubot.daemon.llm import _has_final_response, _should_auto_continue_rollover


def test_has_final_response_detects_text_reply():
    history = [
        ("user", ["问题"]),
        ("assistant", [{"type": "tool_call", "name": "web.read"}]),
        ("assistant", ["最终回复"]),
    ]

    assert _has_final_response(history) is True


def test_has_final_response_ignores_tool_only_turn():
    history = [
        ("user", ["问题"]),
        ("assistant", [{"type": "tool_call", "name": "web.read"}]),
    ]

    assert _has_final_response(history) is False


def test_rollover_auto_continue_only_once():
    history = [
        ("user", ["问题"]),
        ("assistant", [{"type": "tool_call", "name": "web.read"}]),
    ]

    assert _should_auto_continue_rollover({}, history) is True
    assert _should_auto_continue_rollover({"_rollover_auto_count": 1}, history) is False
