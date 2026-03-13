from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.builder import TaskBundle


def test_build_continuation_merges_consecutive_user_messages():
    bundle = TaskBundle(task_text="new input", user_items=["new input"], is_multimodal=False)

    history, trigger = AgentRunner._build_continuation(
        [("user", ["summary prompt"])],
        bundle,
    )

    assert history == [("user", ["summary prompt\n\nnew input"])]
    assert trigger == "summary prompt\n\nnew input"


def test_build_continuation_appends_when_last_message_not_user():
    bundle = TaskBundle(task_text="new input", user_items=["new input"], is_multimodal=False)

    history, trigger = AgentRunner._build_continuation(
        [("assistant", ["done"])],
        bundle,
    )

    assert history == [
        ("assistant", ["done"]),
        ("user", ["new input"]),
    ]
    assert trigger == "new input"
