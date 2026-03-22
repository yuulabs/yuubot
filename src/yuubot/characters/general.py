"""通用助手 — general-purpose system agent (master only)."""

from yuubot.prompt import AgentSpec, CapVisibility, Character
from yuubot.characters import (
    SLEEP_MECHANISM,
    bootstrap_section,
    docker_section,
    register,
    subagents_section,
)

_spec = AgentSpec(
    tools=[
        "execute_bash", "call_cap_cli",
        "edit_file", "read_file",
        "sleep", "delegate",
        "inspect_background", "cancel_background",
        "input_background", "defer_background", "wait_background",
    ],
    sections=[
        subagents_section("coder", "researcher"),
        docker_section(),
        SLEEP_MECHANISM,
        bootstrap_section("/home/yuu/bootstrap.md"),
    ],
    caps=["*"],
    expand_caps=["im"],
    cap_visibility={
        "mem": CapVisibility(mode="include", actions=("save", "recall", "show", "config")),
    },
    subagents=["coder", "researcher"],
    soft_timeout=60,
    silence_timeout=120,
)

register(Character(
    name="general",
    description="通用 Agent，可执行 bash 命令。仅限 Master 使用。",
    min_role="master",
    persona=(
        "你是一个通用系统助手，可以执行 bash 命令和调用各种技能来完成任务。\n"
        "你有完整的系统访问权限，请谨慎操作。\n\n"
        "对于编码任务，使用 delegate 工具委派给 coder agent。\n\n"
        "上下文感知：\n"
        "- 你收到的消息只是 @你 的消息，中间可能有其他对话你没看到\n"
        "- 如果上下文不完整或不连贯，先用 im browse 查看最近聊天记录再行动\n"
        "- 养成习惯：信息不足时先 browse 补全上下文，不要凭空猜测\n\n"
        "错误处理：\n"
        "- 如果工具调用返回了非预期的错误（不是正常的截断、空结果等），必须立即通过 im send 报告给用户\n"
        "- 不要吞掉错误或默默重试，用户需要知道发生了什么才能协助排查"
    ),
    spec=_spec,
    max_tokens=128000,
))
