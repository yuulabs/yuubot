import attrs
import yuuagents as ya

from yuubot.characters import register
from yuubot.prompt import (
    AgentSpec,
    Character,
    ExpandFunctionsSection,
    InlineSection,
    PYTHON_RUNTIME_SECTION,
    PythonWorkerSection,
)

_base_spec = AgentSpec(
    tools=("execute_python",),
    import_modules=(
        ya.PythonImport("yuubot.agent_fns.im", alias="im"),
        ya.PythonImport("yuubot.agent_fns.mem", alias="mem"),
    ),
    prompt_sections=(
        InlineSection("你是 yuubot 的记忆整理 agent，只处理结构化记忆维护。"),
        PYTHON_RUNTIME_SECTION,
        ExpandFunctionsSection(),
        PythonWorkerSection(),
    ),
    max_turns=32,
    max_context_tokens=128000,
    idle_agent_ttl_s=3600,
    preserve_python_session=True,
)

_base = Character(
    name="mem_curator",
    description="记忆整理、去重、归档、恢复和上下文归档。",
    spec=_base_spec,
    bot_kind="master",
)

register(attrs.evolve(_base, name="master_mem_curator"))
register(attrs.evolve(
    _base,
    name="group_mem_curator",
    bot_kind="group",
    spec=attrs.evolve(_base_spec, idle_agent_ttl_s=300, preserve_python_session=False),
))
