from yuubot.daemon.summarizer import build_summary_prompt


def test_build_summary_prompt_continue():
    prompt = build_summary_prompt("原任务", "已发送过回复", should_continue=True)

    assert "<原任务>\n原任务\n</原任务>" in prompt
    assert "<压缩摘要>\n已发送过回复\n</压缩摘要>" in prompt
    assert prompt.endswith("请继续未完成的工作。")


def test_build_summary_prompt_completed():
    prompt = build_summary_prompt("原任务", "任务已完成", should_continue=False)

    assert prompt.endswith("前述任务已完成。若用户有新消息，再基于以上上下文继续处理。")
