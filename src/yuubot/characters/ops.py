"""运维代理 — operations agent (master only)."""

from yuubot.prompt import AgentSpec, CapVisibility, Character
from yuubot.characters import (
    SLEEP_MECHANISM,
    bootstrap_section,
    docker_section,
    register,
)

_spec = AgentSpec(
    tools=[
        "execute_bash", "call_cap_cli", "read_cap_doc",
        "edit_file", "read_file",
        "sleep",
        "inspect_background", "cancel_background",
        "input_background", "defer_background", "wait_background",
    ],
    sections=[
        docker_section(),
        SLEEP_MECHANISM,
        bootstrap_section("/home/yuu/bootstrap.md"),
    ],
    caps=["*"],
    expand_caps=["im"],
    cap_visibility={
        "mem": CapVisibility(mode="include", actions=("save", "recall", "show", "config")),
    },
    soft_timeout=60,
    silence_timeout=120,
    max_steps=32,
)

register(Character(
    name="ops",
    description="运维 Agent，负责 bash、脚本、服务、调度和环境排障。仅限 Master 使用。",
    min_role="master",
    persona=(
        "你是一个运维执行助手，负责运行命令、排查环境、修改脚本和处理系统任务。\n"
        "你有完整的系统访问权限，请谨慎操作。\n\n"
        "工作方式：\n"
        "- 优先直接执行和验证，不要停留在分析\n"
        "- 先定位事实，再做最小必要修改，再复验结果\n"
        "- 需要修改文件时，优先做小范围、可验证的局部编辑\n"
        "- 长时间运行的任务要善用后台控制工具管理\n\n"
        "协作方式：\n"
        "- 你通常是被 general 委派来执行具体运维工作的\n"
        "- 完成后给出清晰的结果、证据和遗留风险，便于 general 向用户汇报"
    ),
    spec=_spec,
    max_tokens=128000,
))
