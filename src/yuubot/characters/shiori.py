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
        name="shiori",
        description="Shiori，Master 私聊中的长期协作伙伴，维护工作区、跟踪项目进展、委派执行任务。",
        spec=AgentSpec(
            tools=("execute_python", "read_file", "edit_file"),
            import_modules=(
                ya.PythonImport("yuubot.agent_fns", alias="yb"),
                ya.PythonImport("yuubot.agent_fns.im", alias="im"),
                ya.PythonImport("yuubot.agent_fns.mem", alias="mem"),
                ya.PythonImport("yuubot.agent_fns.delegate", alias="delegate"),
                ya.PythonImport("yuubot.agent_fns.vision", alias="vision"),
            ),
            expand_functions=("*", "+im.*"),
            prompt_sections=(
                FileSection("shiori/persona.md"),
                FileSection("shiori/messaging.md"),
                PYTHON_RUNTIME_SECTION,
                ExpandFunctionsSection(),
                FileSection("shiori/bootstrap.md"),
                FileSection("shiori/workspace.md"),
                DelegatesSection(),
                PythonWorkerSection(),
            ),
            delegate_policy=DelegatePolicy(allowed_agents=("general",), max_depth=1),
            max_turns=None,
            max_context_tokens=512000,
            idle_agent_ttl_s=3600,
            preserve_python_session=True,
        ),
        bot_kind="master",
    )
)
