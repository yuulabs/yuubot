import yuuagents as ya

from yuubot.characters import register
from yuubot.prompt import (
    AgentSpec,
    Character,
    DelegatePolicy,
    DelegatesSection,
    ExpandFunctionsSection,
    FileSection,
    PYTHON_RUNTIME_SECTION,
    PythonWorkerSection,
)

register(
    Character(
        name="maid",
        description="master 私聊默认 agent，长期维护工作区、跟踪项目进展、委派执行任务。",
        spec=AgentSpec(
            tools=("execute_python", "read_file", "edit_file"),
            import_modules=(
                ya.PythonImport("yuubot.agent_fns", alias="yb"),
                ya.PythonImport("yuubot.agent_fns.im", alias="im"),
                ya.PythonImport("yuubot.agent_fns.mem", alias="mem"),
                ya.PythonImport("yuubot.agent_fns.schedule", alias="schedule"),
                ya.PythonImport("yuubot.agent_fns.delegate", alias="delegate"),
                ya.PythonImport("yuubot.agent_fns.vision", alias="vision"),
            ),
            expand_functions=("+im.*", "*"),
            prompt_sections=(
                FileSection("maid/persona.md"),
                FileSection("maid/messaging.md"),
                PYTHON_RUNTIME_SECTION,
                ExpandFunctionsSection(),
                FileSection("maid/bootstrap.md"),
                FileSection("maid/workspace.md"),
                DelegatesSection(),
                PythonWorkerSection(),
            ),
            delegate_policy=DelegatePolicy(allowed_agents=("general",), max_depth=1),
            max_turns=None,
            max_context_tokens=512000,
        ),
        bot_kind="master",
    )
)
