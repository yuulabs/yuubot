"""编码代理 — coding agent (master only)."""

from yuubot.prompt import AgentSpec, Character
from yuubot.characters import SLEEP_MECHANISM, docker_section, register

_spec = AgentSpec(
    tools=[
        "execute_bash", "read_file", "write_file", "edit_file",
        "sleep", "check_running_tool", "cancel_running_tool",
    ],
    sections=[
        docker_section(),
        SLEEP_MECHANISM,
    ],
    max_steps=30,
    soft_timeout=60,
)

register(Character(
    name="coder",
    description="编码代理，使用外部编码工具完成开发任务。",
    min_role="master",
    persona="你是编码者。你负责根据用户需求编写代码。保持良好的软件工程实践，拒绝劣质代码。",
    spec=_spec,
))
